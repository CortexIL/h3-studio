import { Clapperboard, GripVertical, ListX, SearchX, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

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
import { useT, type Key } from '@/i18n'

import { JobCard } from './JobCard'

type Filter = 'all' | 'active' | 'ready' | 'failed'

/** Drop target meaning "after the last waiting clip". */
const END = '__end__'

const MATCH: Record<Filter, (j: Job) => boolean> = {
  all: () => true,
  active: (j) => j.status === 'queued' || j.status === 'running',
  ready: (j) => j.status === 'done',
  failed: (j) => j.status === 'failed' || j.status === 'cancelled',
}

const LABEL: Record<Filter, Key> = { all: 'feed.all', active: 'feed.active', ready: 'feed.ready', failed: 'feed.failed' }

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
  // No text selection while a dragged handle travels over the other cards.
  useEffect(() => {
    if (dragging === null) return
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'grabbing'
    return () => {
      document.body.style.removeProperty('user-select')
      document.body.style.removeProperty('cursor')
    }
  }, [dragging])
  const t = useT()
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
  // Waiting clips are shown in the order they will be made, next up at the
  // bottom, whatever order the server listed them in; a drag changes their
  // positions and this is what makes the card actually move.
  const visible = useMemo(() => {
    const shown = (list ?? []).filter(MATCH[filter])
    const waiting = shown
      .filter((j) => j.status === 'queued')
      .sort((a, b) => (b.queue_position ?? -1) - (a.queue_position ?? -1))
    return [...waiting, ...shown.filter((j) => j.status !== 'queued')]
  }, [list, filter])
  // The viewer's arrows step through the finished clips in this list.
  useEffect(() => {
    useViewerList.getState().setIds(visible.filter((j) => j.status === 'done' && j.video_url).map((j) => j.id))
  }, [visible])
  const finished = counts.ready + counts.failed
  const queuedIds = useMemo(
    () => visible.filter((j) => j.status === 'queued').map((j) => j.id),
    [visible],
  )

  /** Put `id` where `target` sits in the list (at the end when null), and send the queue's own order. */
  const moveTo = (id: string, target: string | null) => {
    if (id === target) return
    const order = queuedIds.filter((x) => x !== id)
    const at = target ? order.indexOf(target) : -1
    order.splice(at < 0 ? order.length : at, 0, id)
    reorder.mutate([...order].reverse())
  }

  // Dragging is done with pointer events, not the browser's drag-and-drop: that
  // API needs a draggable element to be pressed on its own text, ignores touch,
  // and drags the poster image instead of the card when the press lands on it.
  // A handle with pointer capture works the same with a mouse, a finger or a pen.
  const listRef = useRef<HTMLDivElement>(null)
  const lastQueued = queuedIds[queuedIds.length - 1]

  /** The queued card the pointer is above, by the midpoint of each card; END below them all. */
  const targetAt = (y: number, from: string): string | null => {
    const cards = listRef.current?.querySelectorAll<HTMLElement>('[data-queued]') ?? []
    let seen = false
    for (const el of cards) {
      const id = el.dataset.jobId ?? ''
      if (id === from) continue
      seen = true
      const r = el.getBoundingClientRect()
      if (y < r.top + r.height / 2) return id
    }
    return seen ? END : null
  }

  /** The drag itself, from the handle or from the card body once it has moved. */
  const beginDrag = (id: string, pointerId: number, capture: Element) => {
    if (typeof capture.setPointerCapture === 'function') capture.setPointerCapture(pointerId)
    window.getSelection?.()?.removeAllRanges()
    draggingRef.current = id
    setDragging(id)
    const move = (ev: PointerEvent) => setOver(targetAt(ev.clientY, id))
    const finish = (ev: PointerEvent) => {
      cleanup()
      const target = targetAt(ev.clientY, id)
      if (target) moveTo(id, target === END ? null : target)
      endDrag()
    }
    const cancel = () => {
      cleanup()
      endDrag()
    }
    const key = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') cancel()
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', cancel)
      window.removeEventListener('keydown', key)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', cancel)
    window.addEventListener('keydown', key)
  }

  /** The handle: a press is a drag at once. */
  const startDrag = (e: ReactPointerEvent<HTMLButtonElement>, id: string) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    e.preventDefault()
    beginDrag(id, e.pointerId, e.currentTarget)
  }

  /** The card body: a press arms a drag that begins once the pointer has moved a
   *  little, so clicks and text selection on the card keep working. */
  const armDrag = (e: ReactPointerEvent<HTMLDivElement>, id: string) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    const target = e.target as HTMLElement
    if (target.closest('button, a, input, textarea, select, video, [role="menuitem"]')) return
    const { clientX, clientY, pointerId, currentTarget } = e
    const move = (ev: PointerEvent) => {
      if (Math.abs(ev.clientX - clientX) + Math.abs(ev.clientY - clientY) < 6) return
      cleanup()
      beginDrag(id, pointerId, currentTarget)
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', cleanup)
      window.removeEventListener('pointercancel', cleanup)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', cleanup)
    window.addEventListener('pointercancel', cleanup)
  }

  /** The arrow keys on the handle, because a drag is not reachable from a keyboard. */
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
      title: t('feed.clearTitle'),
      description: t('feed.clearDesc'),
      confirmLabel: t('common.clear'),
      action: () => clear.mutateAsync(),
    })

  return (
    <section aria-label={t('feed.aria')} className="flex min-h-0 flex-1 flex-col rounded-xl border bg-card">
      <header className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2">
        <h2 className="text-sm font-semibold">{t('feed.title')}</h2>
        <div className="flex-1" />
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={filter}
          onValueChange={(v) => v && setFilter(v as Filter)}
          aria-label={t('feed.filter')}
          // On a phone the filters take their own row under the title.
          className="order-last sm:order-none"
        >
          {(Object.keys(LABEL) as Filter[]).map((f) => (
            <ToggleGroupItem key={f} value={f} className="gap-1.5 px-2.5 text-xs">
              {t(LABEL[f])}
              <span className="text-2xs text-muted-foreground tabular-nums">{counts[f]}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <Button size="sm" variant="ghost" onClick={onClear} disabled={!finished || clear.isPending} aria-label={t('feed.clearFinished')}>
          <ListX /> <span className="hidden sm:inline">{t('feed.clearFinished')}</span>
        </Button>
      </header>

      <div className="@container min-h-0 flex-1 overflow-y-auto p-3">
        {jobs.isPending ? (
          <div className="grid gap-3" aria-label={t('feed.loading')}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-40 w-full rounded-lg" />
            ))}
          </div>
        ) : jobs.isError && !list ? (
          <EmptyState
            icon={WifiOff}
            title={t('feed.loadFailed')}
            description={t('feed.loadFailedDesc')}
            action={<Button size="sm" variant="outline" onClick={() => void jobs.refetch()}>{t('common.tryNow')}</Button>}
          />
        ) : visible.length === 0 ? (
          counts.all === 0 ? (
            <EmptyState
              icon={Clapperboard}
              title={t('feed.empty')}
              description={t('feed.emptyDesc')}
            />
          ) : (
            <EmptyState icon={SearchX} title={t('feed.nothingFiltered', { filter: t(LABEL[filter]).toLowerCase() })} action={
              <Button size="sm" variant="outline" onClick={() => setFilter('all')}>{t('feed.showAll')}</Button>
            } />
          )
        ) : (
          <div ref={listRef} className="grid gap-3">
            {visible.map((job) => {
              const queued = job.status === 'queued'
              return (
                <div
                  key={job.id}
                  data-job-id={job.id}
                  data-queued={queued ? '' : undefined}
                  data-drop-target={over === job.id && dragging !== job.id ? '' : undefined}
                  onPointerDown={queued ? (e) => armDrag(e, job.id) : undefined}
                  className={cn(
                    'relative rounded-lg transition-opacity',
                    queued && 'cursor-grab',
                    dragging === job.id && 'opacity-40',
                    // Where it will land, drawn in the gap above the card (or under the
                    // last one) so nothing moves until the pointer is released.
                    over === job.id && dragging !== job.id &&
                      'before:absolute before:inset-x-0 before:-top-1.5 before:h-0.5 before:rounded-full before:bg-primary before:content-[\'\']',
                    over === END && job.id === lastQueued && dragging !== job.id &&
                      'after:absolute after:inset-x-0 after:-bottom-1.5 after:h-0.5 after:rounded-full after:bg-primary after:content-[\'\']',
                  )}
                >
                  {queued ? (
                    <button
                      type="button"
                      aria-label={t('feed.reorder', { prompt: job.prompt.slice(0, 60) })}
                      aria-describedby={queuedIds.length > 1 ? 'reorder-hint' : undefined}
                      title={t('feed.dragHandle')}
                      onPointerDown={(e) => startDrag(e, job.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'ArrowUp') {
                          e.preventDefault()
                          nudge(job.id, -1)
                        } else if (e.key === 'ArrowDown') {
                          e.preventDefault()
                          nudge(job.id, 1)
                        }
                      }}
                      className="absolute top-2 start-2 z-10 grid size-7 cursor-grab touch-none place-items-center rounded-md border border-white/20 bg-black/60 text-white/80 outline-none backdrop-blur-sm hover:text-white focus-visible:ring-[3px] focus-visible:ring-ring/50 active:cursor-grabbing"
                    >
                      <GripVertical className="size-4" />
                    </button>
                  ) : null}
                  <JobCard job={job} />
                </div>
              )
            })}
            {queuedIds.length > 1 ? (
              <p id="reorder-hint" className="px-1 text-center text-2xs text-faint">
                {t('feed.reorderHint')}
              </p>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}
