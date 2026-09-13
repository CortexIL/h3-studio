import { ChevronLeft, ChevronRight, Copy, Download, FileX, Maximize2, Repeat, RotateCcw, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { useDeleteClip, useRunAgain, useUpscale } from '@/api/mutations'
import { useJob, useStatus } from '@/api/queries'
import type { Job } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { RefThumb } from '@/components/app/RefThumb'
import { EmptyState } from '@/components/app/EmptyState'
import { StatusBadge } from '@/components/app/StatusBadge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useCompose } from '@/features/studio/composeStore'
import { fmtBytes, fmtWhen, modeLabel, presetLabel, presetSize } from '@/lib/format'
import { downloadUrl, imageUrl } from '@/lib/media'
import { cn } from '@/lib/utils'
import { useT } from '@/i18n'

import { useClipViewer, useViewerList } from './useClipViewer'

/** Arrow keys belong to whatever has focus if it uses them - a video seeks, a field moves its caret. */
function ownsArrows(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT', 'VIDEO'].includes(target.tagName))
  )
}

function Viewer({ job, neighbor }: { job: Job; neighbor: string | undefined }) {
  const { close, go } = useClipViewer()
  const t = useT()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const again = useRunAgain()
  const upscale = useUpscale()
  const deleteClip = useDeleteClip()
  const preset = useStatus().data?.config.presets[job.preset]
  const videoUrl = job.status === 'done' ? job.video_url : null

  const copy = () =>
    navigator.clipboard.writeText(job.prompt).then(
      () => toast.success(t('viewer.promptCopied')),
      () => toast.error(t('common.copyFailed')),
    )

  const useAgain = () => {
    useCompose.getState().loadFromJob(job)
    navigate('/')
    toast.success(t('clip.copiedToForm'))
  }

  const onDelete = () =>
    void confirm({
      title: t('clip.deleteTitle'),
      description: t('clip.deleteDesc'),
      confirmLabel: t('clip.deleteConfirm'),
      destructive: true,
      action: async () => {
        await deleteClip.mutateAsync(job.id)
        if (neighbor) go(neighbor)
        else close()
      },
    })

  // A full date doesn't fit half the panel, so it gets a row of its own.
  const meta: { label: string; value: string; wide?: boolean }[] = [
    { label: t('viewer.length'), value: t('time.seconds', { n: job.seconds }) },
    { label: t('viewer.quality'), value: preset ? `${presetLabel(job.preset)} · ${presetSize(preset)}` : presetLabel(job.preset) },
    { label: t('viewer.mode'), value: modeLabel(job.mode) },
    // Only when the clip actually recorded a choice. null means it followed
    // whatever the server did at the time, which is not something to assert -
    // and clips made before the switch existed all carry null.
    ...(job.keep_audio === null
      ? []
      : [{ label: t('viewer.sound'), value: t(job.keep_audio ? 'common.on' : 'common.off') }]),
    ...(job.sound ? [{ label: t('viewer.soundscape'), value: job.sound, wide: true }] : []),
    ...(job.music ? [{ label: t('viewer.music'), value: job.music, wide: true }] : []),
    ...(job.steps ? [{ label: t('viewer.steps'), value: String(job.steps) }] : []),
    ...(job.shift_video || job.shift_audio
      ? [{ label: t('viewer.motion'), value: t('viewer.motionValue', { video: job.shift_video ?? 12, audio: job.shift_audio ?? 3 }) }]
      : []),
    ...(job.width && job.height ? [{ label: t('viewer.renderSize'), value: `${job.width}×${job.height}` }] : []),
    ...(job.keyframes.length ? [{ label: t('viewer.keyframes'), value: job.keyframes.map((k) => t('time.seconds', { n: k.at })).join(', ') }] : []),
    ...(job.audio ? [{ label: t('viewer.audioTrack'), value: t('viewer.followed') }] : []),
    ...(job.ref_videos.length || job.ref_audios.length
      ? [{ label: t('viewer.references'), value: t('viewer.refsValue', { images: job.ref_images.length, videos: job.ref_videos.length, audios: job.ref_audios.length }) }]
      : []),
    { label: t('viewer.seed'), value: job.seed === null ? t('common.random') : String(job.seed) },
    { label: t('viewer.size'), value: fmtBytes(job.bytes) },
    { label: t('viewer.made'), value: fmtWhen(job.finished_at ?? job.created_at), wide: true },
  ]

  return (
    <div className="grid lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="grid min-h-48 place-items-center bg-black">
        {videoUrl ? (
          <video
            src={videoUrl}
            poster={job.poster_url ?? undefined}
            controls
            autoPlay
            playsInline
            aria-label={t('job.clipLabel', { prompt: job.prompt.slice(0, 80) })}
            className="block max-h-[72dvh] w-full object-contain"
          />
        ) : (
          <div className="grid justify-items-center gap-2 p-10 text-center">
            <StatusBadge status={job.status} />
            <p className="text-sm text-muted-foreground">
              {t(job.status === 'done' ? 'job.gone' : 'viewer.pending')}
            </p>
          </div>
        )}
      </div>

      <aside className="flex min-w-0 flex-col gap-5 border-t p-5 lg:border-t-0 lg:border-s">
        <section className="grid gap-1.5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium text-muted-foreground">{t('viewer.prompt')}</h3>
            <Button size="sm" variant="ghost" className="h-7 gap-1.5 px-2 text-xs" onClick={copy}>
              <Copy className="size-3.5" /> {t('common.copy')}
            </Button>
          </div>
          <p className="max-h-48 overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap">{job.prompt}</p>
        </section>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
          {meta.map(({ label, value, wide }) => (
            <div key={label} className={cn('min-w-0', wide && 'col-span-2')}>
              <dt className="text-2xs text-muted-foreground">{label}</dt>
              <dd className="truncate tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>

        {job.ref_images.length ? (
          <section className="grid gap-1.5">
            <h3 className="text-xs font-medium text-muted-foreground">{t('viewer.references')}</h3>
            <div className="flex flex-wrap gap-2">
              {job.ref_images.map((key) => (
                <a
                  key={key}
                  href={imageUrl(key)}
                  target="_blank"
                  rel="noreferrer"
                  className="block size-14 overflow-hidden rounded-md border transition-colors hover:border-primary/60"
                >
                  <RefThumb objectKey={key} alt={t('viewer.reference')} className="size-full" />
                </a>
              ))}
            </div>
          </section>
        ) : null}

        <div className="mt-auto grid grid-cols-2 gap-2">
          {videoUrl ? (
            <Button asChild className="col-span-2">
              <a href={downloadUrl(videoUrl)} download>
                <Download /> {t('common.download')}
              </a>
            </Button>
          ) : null}
          <Button variant="outline" onClick={useAgain}>
            <RotateCcw /> {t('job.useAgain')}
          </Button>
          {job.status === 'done' && job.video_url && job.mode !== 'upscale' ? (
            <>
              <Button variant="outline" onClick={() => upscale.mutate({ id: job.id, deliver: '2x' })} disabled={upscale.isPending}>
                <Maximize2 /> {t('job.upscale2x')}
              </Button>
              <Button variant="outline" onClick={() => upscale.mutate({ id: job.id, deliver: '1080p' })} disabled={upscale.isPending}>
                <Maximize2 /> {t('viewer.upscale1080Short')}
              </Button>
            </>
          ) : null}
          <Button variant="outline" onClick={() => again.mutate(job.id)} disabled={again.isPending}>
            <Repeat /> {t('clip.runAgain')}
          </Button>
          {videoUrl ? (
            <Button
              variant="ghost"
              className="col-span-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={onDelete}
            >
              <Trash2 /> {t('clip.delete')}
            </Button>
          ) : null}
        </div>
      </aside>
    </div>
  )
}

function ViewerBody({ id, neighbor }: { id: string; neighbor: string | undefined }) {
  const t = useT()
  const job = useJob(id)
  const { close } = useClipViewer()
  if (job.isPending) {
    return (
      <div className="grid lg:grid-cols-[minmax(0,1fr)_340px]" aria-label={t('viewer.loading')}>
        <Skeleton className="aspect-video rounded-none" />
        <div className="grid content-start gap-3 p-5">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    )
  }
  if (!job.data) {
    return (
      <EmptyState
        icon={FileX}
        title={t('viewer.unavailable')}
        description={t('viewer.unavailableDesc')}
        action={
          <Button size="sm" variant="outline" onClick={close}>
            {t('common.close')}
          </Button>
        }
      />
    )
  }
  return <Viewer job={job.data} neighbor={neighbor} />
}

/** One viewer for the whole app, opened by ?clip=<id> from the Studio or the Archive. */
export function ClipViewerDialog() {
  const t = useT()
  const { id, go, close } = useClipViewer()
  const ids = useViewerList((s) => s.ids)
  const index = id ? ids.indexOf(id) : -1
  const prevId = index > 0 ? ids[index - 1] : undefined
  const nextId = index >= 0 ? ids[index + 1] : undefined

  return (
    <Dialog
      open={id !== null}
      onOpenChange={(open) => {
        if (!open) close()
      }}
    >
      <DialogContent
        showCloseButton={false}
        className="max-h-[calc(100dvh-2rem)] gap-0 overflow-y-auto p-0 sm:max-w-5xl"
        onKeyDown={(e) => {
          if (ownsArrows(e.target) || e.altKey || e.metaKey || e.ctrlKey) return
          if (e.key === 'ArrowLeft' && prevId) {
            e.preventDefault()
            go(prevId)
          } else if (e.key === 'ArrowRight' && nextId) {
            e.preventDefault()
            go(nextId)
          }
        }}
      >
        <header className="flex h-12 items-center gap-1 border-b px-3">
          <Button size="icon" variant="ghost" className="size-8" disabled={!prevId} onClick={() => prevId && go(prevId)} aria-label={t('viewer.prev')}>
            <ChevronLeft className="size-4 rtl:rotate-180" />
          </Button>
          <Button size="icon" variant="ghost" className="size-8" disabled={!nextId} onClick={() => nextId && go(nextId)} aria-label={t('viewer.next')}>
            <ChevronRight className="size-4 rtl:rotate-180" />
          </Button>
          <DialogTitle className="ms-1 text-sm font-medium">
            {index >= 0 && ids.length > 1 ? t('viewer.counter', { i: index + 1, n: ids.length }) : t('viewer.clip')}
          </DialogTitle>
          <DialogDescription className="sr-only">{t('viewer.srDesc')}</DialogDescription>
          <div className="flex-1" />
          <Button size="icon" variant="ghost" className="size-8" onClick={close} aria-label={t('common.close')}>
            <X className="size-4" />
          </Button>
        </header>
        {id ? <ViewerBody key={id} id={id} neighbor={nextId ?? prevId} /> : null}
      </DialogContent>
    </Dialog>
  )
}
