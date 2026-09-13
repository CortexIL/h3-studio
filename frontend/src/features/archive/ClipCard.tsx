import { Check, Download, Expand, Film, MoreHorizontal, Play, Repeat, RotateCcw, Trash2 } from 'lucide-react'
import { memo, useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { useDeleteClip, useRunAgain } from '@/api/mutations'
import type { Clip } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCompose } from '@/features/studio/composeStore'
import { useClipViewer } from '@/features/viewer/useClipViewer'
import { fmtRelative, fmtWhen, modeLabel, presetLabel } from '@/lib/format'
import { downloadUrl } from '@/lib/media'
import { cn } from '@/lib/utils'
import { useT } from '@/i18n'

function Poster({ clip }: { clip: Clip }) {
  const [failed, setFailed] = useState(false)
  if (!clip.poster_url || failed) {
    return (
      <div className="grid size-full place-items-center bg-gradient-to-br from-secondary to-background text-faint">
        <Film className="size-6" />
      </div>
    )
  }
  return (
    <img
      src={clip.poster_url}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
    />
  )
}

interface ClipCardProps {
  clip: Clip
  selected?: boolean
  /** Takes the id so the handler stays stable and the card stays memoised. */
  onPick?: (id: string, event: { shiftKey: boolean }) => void
}

function ClipCardImpl({ clip, selected = false, onPick }: ClipCardProps) {
  const { open } = useClipViewer()
  const navigate = useNavigate()
  const t = useT()
  const confirm = useConfirm()
  const again = useRunAgain()
  const deleteClip = useDeleteClip()
  const when = clip.finished_at ?? clip.created_at

  const useInStudio = () => {
    useCompose.getState().loadFromJob(clip)
    navigate('/')
    toast.success(t('clip.copiedToForm'))
  }

  const onDelete = () =>
    void confirm({
      title: t('clip.deleteTitle'),
      description: t('clip.deleteDesc'),
      confirmLabel: t('clip.deleteConfirm'),
      destructive: true,
      action: () => deleteClip.mutateAsync(clip.id),
    })

  return (
    <article
      data-clip-id={clip.id}
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-lg border bg-card transition-colors',
        selected ? 'border-primary ring-2 ring-primary/40' : 'hover:border-primary/40',
      )}
    >
      {onPick ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onPick(clip.id, e)
          }}
          aria-pressed={selected}
          data-no-band
          aria-label={`${t(selected ? 'clip.deselect' : 'clip.select')}: ${clip.prompt.slice(0, 60)}`}
          className={cn(
            'absolute top-2 start-2 z-10 grid size-6 place-items-center rounded-md border transition-opacity',
            selected
              ? 'border-primary bg-primary text-primary-foreground'
              : 'border-white/50 bg-black/50 text-white opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
          )}
        >
          {selected ? <Check className="size-4" /> : null}
        </button>
      ) : null}
      <button
        type="button"
        onClick={(e) => {
          // Cmd, Ctrl or Shift turns a click into a pick, as in any file list.
          if (onPick && (e.metaKey || e.ctrlKey || e.shiftKey)) {
            e.preventDefault()
            onPick(clip.id, e)
            return
          }
          open(clip.id)
        }}
        className="relative block aspect-video w-full overflow-hidden bg-black"
        aria-label={t('clip.open', { prompt: clip.prompt.slice(0, 80) })}
      >
        <Poster clip={clip} />
        <span className="absolute inset-0 grid place-items-center bg-black/0 transition-colors group-hover:bg-black/25">
          <span className="grid size-11 place-items-center rounded-full bg-black/60 text-white opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            <Play className="size-5 translate-x-px fill-current" />
          </span>
        </span>
        <span className="absolute end-2 bottom-2 rounded bg-black/70 px-1.5 py-0.5 text-2xs font-medium text-white tabular-nums">
          {t('time.seconds', { n: clip.seconds })}
        </span>
      </button>
      <div className="flex flex-1 items-start gap-1 p-3">
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm leading-snug">{clip.prompt}</p>
          <p className="mt-1.5 truncate text-2xs text-muted-foreground">
            {presetLabel(clip.preset)} · {modeLabel(clip.mode)} ·{' '}
            <time title={fmtWhen(when)}>{fmtRelative(when)}</time>
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon" variant="ghost" data-no-band className="-me-1 size-8 shrink-0" aria-label={t('clip.actions')}>
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem onSelect={() => open(clip.id)}>
              <Expand /> {t('common.open')}
            </DropdownMenuItem>
            {clip.video_url ? (
              <DropdownMenuItem asChild>
                <a href={downloadUrl(clip.video_url)} download>
                  <Download /> {t('common.download')}
                </a>
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuItem onSelect={useInStudio}>
              <RotateCcw /> {t('clip.useInStudio')}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => again.mutate(clip.id)}>
              <Repeat /> {t('clip.runAgain')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              <Trash2 /> {t('clip.delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </article>
  )
}

export const ClipCard = memo(ClipCardImpl)
