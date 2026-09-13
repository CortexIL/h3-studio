import {
  AlertTriangle,
  Ban,
  Clock,
  Download,
  Expand,
  MoreHorizontal,
  Repeat,
  RotateCcw,
  RotateCw,
  Maximize2,
  Trash2,
  X,
  EyeOff,
} from 'lucide-react'
import { memo } from 'react'
import { toast } from 'sonner'

import { useCancelJob, useDeleteClip, useRemoveFromFeed, useRetryJob, useRunAgain, useUpscale } from '@/api/mutations'
import type { Job } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { Elapsed } from '@/components/app/Elapsed'
import { LazyVideo } from '@/components/app/LazyVideo'
import { RefThumb } from '@/components/app/RefThumb'
import { StatusBadge } from '@/components/app/StatusBadge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useClipViewer } from '@/features/viewer/useClipViewer'
import { MODE_LABEL, presetLabel } from '@/lib/format'
import { downloadUrl } from '@/lib/media'
import { cn } from '@/lib/utils'

import { useCompose } from './composeStore'

function Stage({ job, onRetry }: { job: Job; onRetry: () => void }) {
  const { open } = useClipViewer()
  if (job.status === 'done' && job.video_url) {
    return (
      <div className="relative grid place-items-center bg-black">
        <LazyVideo src={job.video_url} poster={job.poster_url} label={`Clip: ${job.prompt.slice(0, 80)}`} />
        <Button
          size="icon"
          variant="secondary"
          className="absolute top-2 right-2 size-8 bg-black/60 hover:bg-black/80"
          aria-label="Open in the viewer"
          onClick={() => open(job.id)}
        >
          <Expand className="size-4" />
        </Button>
      </div>
    )
  }
  const firstRef = job.ref_images[0]
  return (
    // Nothing to watch yet, so no need for a full 16:9 box: short when
    // stacked, and as tall as the details beside it otherwise.
    <div className="relative grid aspect-[12/5] place-items-center overflow-hidden bg-background @lg:aspect-auto @lg:min-h-36">
      {firstRef ? <RefThumb objectKey={firstRef} className="absolute inset-0 size-full opacity-20" /> : null}
      {job.status === 'running' ? (
        <div className="absolute inset-0 animate-pulse bg-gradient-to-r from-transparent via-info/10 to-transparent" />
      ) : null}
      <div className="relative grid justify-items-center gap-1.5 px-4 text-center">
        {job.status === 'queued' ? (
          <>
            <Clock className="size-5 text-muted-foreground" />
            <p className="text-sm font-medium">Waiting in the queue</p>
            <p className="text-xs text-muted-foreground">
              {job.queue_position === 0
                ? 'Next up'
                : job.queue_position
                  ? `${job.queue_position} ahead of you in the shared queue`
                  : 'In the shared queue'}{' '}
              · nothing is charged until it starts
            </p>
          </>
        ) : job.status === 'running' ? (
          <>
            <RotateCw className="size-5 animate-spin text-info" />
            <p className="text-sm font-medium">Generating</p>
            <p className="text-xs text-muted-foreground">{job.started_at ? <Elapsed since={job.started_at} /> : 'Starting'}</p>
          </>
        ) : job.status === 'failed' ? (
          <>
            <AlertTriangle className="size-5 text-destructive" />
            <p className="text-sm font-medium">This clip couldn't be rendered</p>
            {job.error ? <p className="line-clamp-2 max-w-xs text-xs text-muted-foreground">{job.error}</p> : null}
            <Button size="sm" variant="outline" className="mt-1" onClick={onRetry}>
              <RotateCw /> Retry
            </Button>
          </>
        ) : job.status === 'cancelled' ? (
          <>
            <Ban className="size-5 text-muted-foreground" />
            <p className="text-sm font-medium text-muted-foreground">Cancelled</p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Finished, but the file is no longer stored.</p>
        )}
      </div>
    </div>
  )
}

function JobCardImpl({ job }: { job: Job }) {
  const confirm = useConfirm()
  const { open } = useClipViewer()
  const cancel = useCancelJob()
  const remove = useRemoveFromFeed()
  const retry = useRetryJob()
  const again = useRunAgain()
  const upscale = useUpscale()
  const deleteClip = useDeleteClip()

  const useAgain = () => {
    useCompose.getState().loadFromJob(job)
    document.getElementById('compose-prompt')?.focus()
    toast.success('Copied into the form. Edit it, then add it to the queue.')
  }

  const onCancel = () => {
    if (job.status !== 'running') {
      cancel.mutate(job.id)
      return
    }
    void confirm({
      title: 'Cancel this clip?',
      description: "It's generating right now. The GPU time it has used so far is still billed.",
      confirmLabel: 'Cancel clip',
      cancelLabel: 'Keep it',
      destructive: true,
      action: () => cancel.mutateAsync(job.id),
    })
  }

  const onDelete = () =>
    void confirm({
      title: 'Delete this clip?',
      description: "The video is removed from your Archive and deleted from storage. This can't be undone.",
      confirmLabel: 'Delete clip',
      destructive: true,
      action: () => deleteClip.mutateAsync(job.id),
    })

  const isDone = job.status === 'done' && Boolean(job.video_url)
  const active = job.status === 'queued' || job.status === 'running'

  return (
    <article
      className={cn(
        'grid overflow-hidden rounded-lg border bg-card @lg:grid-cols-[minmax(200px,42%)_1fr]',
        job.status === 'running' && 'border-info/35',
      )}
    >
      <Stage job={job} onRetry={() => retry.mutate(job.id)} />
      <div className="flex min-w-0 flex-col gap-3 p-3.5">
        <p className="line-clamp-4 text-sm leading-relaxed whitespace-pre-wrap">{job.prompt}</p>
        <div className="flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
          <StatusBadge status={job.status} />
          <span className="rounded-full border px-2 py-0.5">{job.seconds}s</span>
          <span className="rounded-full border px-2 py-0.5">{presetLabel(job.preset)}</span>
          <span className="rounded-full border px-2 py-0.5">{MODE_LABEL[job.mode]}</span>
        </div>
        {job.ref_images.length ? (
          <div className="flex gap-1.5">
            {job.ref_images.slice(0, 4).map((key) => (
              <RefThumb key={key} objectKey={key} className="size-9 rounded border" />
            ))}
          </div>
        ) : null}
        <div className="mt-auto flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={useAgain}>
            <RotateCcw /> Use again
          </Button>
          {isDone && job.video_url ? (
            <Button size="sm" variant="ghost" asChild>
              <a href={downloadUrl(job.video_url)} download>
                <Download /> Download
              </a>
            </Button>
          ) : null}
          {active ? (
            <Button size="sm" variant="ghost" onClick={onCancel} disabled={cancel.isPending}>
              <X /> Cancel
            </Button>
          ) : null}
          <div className="flex-1" />
          {job.status !== 'running' ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="icon" variant="ghost" className="size-8" aria-label="More actions">
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                {isDone ? (
                  <>
                    <DropdownMenuItem onSelect={() => open(job.id)}>
                      <Expand /> Open
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => again.mutate(job.id)}>
                      <Repeat /> Run again
                    </DropdownMenuItem>
                    {job.mode !== 'upscale' ? (
                      <>
                        <DropdownMenuItem onSelect={() => upscale.mutate({ id: job.id, deliver: '2x' })}>
                          <Maximize2 /> Upscale 2×
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => upscale.mutate({ id: job.id, deliver: '1080p' })}>
                          <Maximize2 /> Upscale to 1080p
                        </DropdownMenuItem>
                      </>
                    ) : null}
                    <DropdownMenuSeparator />
                  </>
                ) : null}
                {job.status === 'failed' || job.status === 'cancelled' ? (
                  <DropdownMenuItem onSelect={() => retry.mutate(job.id)}>
                    <RotateCw /> Retry
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onSelect={() => remove.mutate(job.id)}>
                  <EyeOff /> {job.status === 'queued' ? 'Remove from the queue' : 'Remove from this list'}
                </DropdownMenuItem>
                {isDone ? (
                  <DropdownMenuItem variant="destructive" onSelect={onDelete}>
                    <Trash2 /> Delete clip…
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      </div>
    </article>
  )
}

/**
 * Memoised on the job object itself. React Query keeps an unchanged job the
 * same object across polls, so a card - and the video playing in it - is left
 * alone unless its own job changed.
 */
export const JobCard = memo(JobCardImpl)
