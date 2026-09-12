import { ArrowRight, Film, ImagePlus, Loader2, RotateCw, Sparkles, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { toast } from 'sonner'

import { useAddJobs } from '@/api/mutations'
import { useEstimate, useStatus } from '@/api/queries'
import type { Mode, NewJobsBody } from '@/api/types'
import { NumberStepper } from '@/components/app/NumberStepper'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MODE_LABEL, fmtDuration, fmtUsd, presetLabel, presetSize } from '@/lib/format'
import { useDebouncedValue } from '@/lib/hooks'
import { imageUrl } from '@/lib/media'
import { COMPOSE_MODES } from '@/lib/modes'
import { cn } from '@/lib/utils'

import type { Block, RefTile } from './composeStore'
import { SECONDS, TAKES, blockedBy, clipCount, draftOf, tilesUsedBy, toPayload, useCompose } from './composeStore'
import { ExtendSourceDialog } from './ExtendSourceDialog'
import { useFilePaste, useReferenceUploads } from './useReferenceUploads'

// What each mode asks for, said in the picker so the block below is never a surprise.
const MODE_HINT: Partial<Record<Mode, string>> = {
  i2v: 'Images and a prompt',
  t2v: 'Prompt only',
  flf2v: 'A start and an end frame',
  extend: 'Continue a finished clip',
}

// Why the button is off. 'prompt' is deliberately absent: an empty prompt box
// already says so itself, and the button keeps its usual name.
const BLOCK_LABEL: Partial<Record<Block, string>> = {
  uploading: 'Waiting for uploads…',
  'upload-failed': 'An upload failed — retry it',
  'start-frame': 'Add a start frame',
  'end-frame': 'Add an end frame',
  'extend-source': 'Choose a video to continue',
}

/** One picked image, wherever it sits: a reference, a start frame, an end frame. */
function ImageTile({ tile, className }: { tile: RefTile; className?: string }) {
  const { retry, remove, canRetry } = useReferenceUploads()
  return (
    <div className={cn('group relative overflow-hidden rounded-md border bg-field', className)} title={tile.name}>
      <img
        src={tile.previewUrl ?? (tile.key ? imageUrl(tile.key) : undefined)}
        alt={tile.name}
        className={cn('size-full object-cover', tile.status !== 'ready' && 'opacity-50')}
      />
      {tile.status === 'uploading' ? (
        <div className="absolute inset-x-1 bottom-1 h-1 overflow-hidden rounded-full bg-black/60" aria-label={`Uploading ${tile.name}`}>
          <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round(tile.progress * 100)}%` }} />
        </div>
      ) : null}
      {tile.status === 'error' ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => canRetry(tile.id) && retry(tile.id)}
              className="absolute inset-0 grid place-items-center bg-destructive/25 text-destructive"
              aria-label={`Retry uploading ${tile.name}`}
            >
              <RotateCw className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{tile.error ?? 'Upload failed'} · click to retry</TooltipContent>
        </Tooltip>
      ) : null}
      <button
        type="button"
        onClick={() => remove(tile.id)}
        aria-label={`Remove ${tile.name}`}
        className="absolute top-1 right-1 grid size-5 place-items-center rounded-full bg-black/70 text-white opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
      >
        <X className="size-3" />
      </button>
    </div>
  )
}

/** One of the two frames Start to end needs, with its own picker. */
function FrameSlot({ slot, label }: { slot: 'start' | 'end'; label: string }) {
  const tile = useCompose((s) => (slot === 'start' ? s.startFrame : s.endFrame))
  const { handleFiles } = useReferenceUploads()
  const input = useRef<HTMLInputElement>(null)
  return (
    <div className="flex-1">
      <input
        ref={input}
        type="file"
        hidden
        accept="image/*"
        onChange={(e) => {
          if (e.target.files) handleFiles(e.target.files, slot)
          e.target.value = ''
        }}
      />
      {tile ? (
        <ImageTile tile={tile} className="aspect-video w-full" />
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          className="grid aspect-video w-full place-items-center rounded-md border border-dashed text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
          aria-label={`Add ${label.toLowerCase()}`}
        >
          <ImagePlus className="size-5" />
        </button>
      )}
      <p className="mt-1 text-center text-2xs text-muted-foreground">{label}</p>
    </div>
  )
}

/** The clip an extension continues: one from the Archive, or one uploaded. */
function ExtendSlot() {
  const source = useCompose((s) => s.extendSource)
  const { handleFiles, remove, retry, canRetry } = useReferenceUploads()
  const input = useRef<HTMLInputElement>(null)
  const [picking, setPicking] = useState(false)

  return (
    <div className="grid gap-2">
      <Label>Video to continue</Label>
      <input
        ref={input}
        type="file"
        hidden
        accept="video/*"
        onChange={(e) => {
          if (e.target.files) handleFiles(e.target.files)
          e.target.value = ''
        }}
      />
      {source ? (
        <div className="flex items-center gap-3 rounded-md border bg-field p-2">
          <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded bg-black">
            {source.posterUrl ? (
              <img src={source.posterUrl} alt="" className="size-full object-cover" />
            ) : source.tile.previewUrl ? (
              <video src={source.tile.previewUrl} preload="metadata" muted className="size-full object-cover" />
            ) : (
              <div className="grid size-full place-items-center">
                <Film className="size-4 text-muted-foreground" />
              </div>
            )}
            {source.tile.status === 'uploading' ? (
              <div className="absolute inset-x-1 bottom-1 h-1 overflow-hidden rounded-full bg-black/60">
                <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round(source.tile.progress * 100)}%` }} />
              </div>
            ) : null}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{source.label}</p>
            <p className={cn('text-2xs', source.tile.status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
              {source.tile.status === 'error'
                ? (source.tile.error ?? 'Upload failed')
                : source.tile.status === 'uploading'
                  ? 'Uploading…'
                  : source.from === 'clip'
                    ? 'From your Archive'
                    : 'Uploaded video'}
            </p>
          </div>
          {source.tile.status === 'error' && canRetry(source.tile.id) ? (
            <Button size="sm" variant="ghost" onClick={() => retry(source.tile.id)}>
              Retry
            </Button>
          ) : null}
          <Button
            size="icon"
            variant="ghost"
            className="size-8"
            aria-label="Remove the source video"
            onClick={() => remove(source.tile.id)}
          >
            <X className="size-4" />
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button variant="outline" className="flex-1 gap-2" onClick={() => setPicking(true)}>
            <Film className="size-4" /> From your clips
          </Button>
          <Button variant="outline" className="flex-1 gap-2" onClick={() => input.current?.click()}>
            <Upload className="size-4" /> Upload a video
          </Button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        The new clip starts where this one ends, carrying its movement and sound. The overlap is
        trimmed off, so the two join without a gap.
      </p>
      <ExtendSourceDialog open={picking} onOpenChange={setPicking} />
    </div>
  )
}

function RefTiles({ onPick }: { onPick: () => void }) {
  const refs = useCompose((s) => s.refs)
  return (
    <div className="flex flex-wrap gap-2">
      {refs.map((r) => (
        <ImageTile key={r.id} tile={r} className="size-16" />
      ))}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onPick}
            className="grid size-16 place-items-center rounded-md border border-dashed text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
            aria-label="Add reference images or a batch file"
          >
            <ImagePlus className="size-5" />
          </button>
        </TooltipTrigger>
        <TooltipContent>Images, a .zip, or a .txt / .json batch. You can also drop or paste them.</TooltipContent>
      </Tooltip>
    </div>
  )
}

function EstimateLine({ body }: { body: NewJobsBody }) {
  const debounced = useDebouncedValue(body, 350)
  const estimate = useEstimate(debounced.prompts.trim() ? debounced : null)
  if (!body.prompts.trim()) return null
  if (!estimate.data) {
    return estimate.isError ? null : <Skeleton className="h-9 w-full" />
  }
  const e = estimate.data
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border bg-background/40 px-3 py-2 text-xs text-muted-foreground" aria-live="polite">
      <span>
        <b className="text-foreground tabular-nums">{e.clips}</b> {e.clips === 1 ? 'clip' : 'clips'}
      </span>
      <span>
        about{' '}
        <b className="text-foreground tabular-nums">
          {e.total_minutes >= 1 ? `${Math.round(e.total_minutes)} min` : fmtDuration(e.total_minutes * 60)}
        </b>
      </span>
      <span>
        <b className="text-foreground tabular-nums">{fmtUsd(e.cost_usd)}</b> ({fmtUsd(e.cost_per_clip_usd, 3)} each)
      </span>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="outline" className={cn('ml-auto', e.confidence === 'measured' ? 'border-ok/40 text-ok' : 'text-muted-foreground')}>
            {e.confidence === 'measured' ? 'Measured' : 'Estimated'}
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-64">
          {e.confidence === 'measured'
            ? 'Based on real timings from this account.'
            : 'A projection from reference timings. An admin can calibrate it with a real run.'}
        </TooltipContent>
      </Tooltip>
    </div>
  )
}

export function ComposePanel() {
  const config = useStatus().data?.config
  const s = useCompose()
  const add = useAddJobs()
  const { handleFiles } = useReferenceUploads()
  useFilePaste(handleFiles)
  const fileInput = useRef<HTMLInputElement>(null)
  const [dragDepth, setDragDepth] = useState(0)

  useEffect(() => {
    if (config) useCompose.getState().applyDefaults(config)
  }, [config])

  const count = clipCount(s.prompt, s.split, s.takes)
  const block = blockedBy(s)
  const usedKeys = tilesUsedBy(s).map((t) => t.key ?? t.id).join('|')

  const payload = useMemo(
    () => toPayload(s),
    // usedKeys stands in for the tiles, which are new objects on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [s.prompt, s.split, s.seconds, s.preset, s.mode, s.takes, s.keepAudio, usedKeys],
  )

  // Priced fields only. The server ignores images when estimating, so the price
  // must not be re-fetched every time one finishes uploading.
  const estimateBody = useMemo<NewJobsBody>(
    () => ({ prompts: s.prompt, split: s.split, seconds: s.seconds, preset: s.preset, mode: s.mode, count: s.takes }),
    [s.prompt, s.split, s.seconds, s.preset, s.mode, s.takes],
  )

  const submit = () => {
    if (blockedBy(useCompose.getState()) || add.isPending) return
    add.mutate(payload, { onSuccess: () => useCompose.getState().clearDraft() })
  }

  const clear = () => {
    const snapshot = draftOf(useCompose.getState())
    if (!snapshot.prompt && !snapshot.refs.length && !snapshot.startFrame && !snapshot.endFrame) return
    useCompose.getState().clearDraft()
    toast('Form cleared', { action: { label: 'Undo', onClick: () => useCompose.getState().restore(snapshot) } })
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragDepth(0)
    if (event.dataTransfer.files.length) handleFiles(event.dataTransfer.files)
  }
  const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')

  return (
    <section
      aria-label="Create"
      className="relative flex min-h-0 flex-1 flex-col rounded-xl border bg-card"
      onDragEnter={(e) => hasFiles(e) && setDragDepth((d) => d + 1)}
      onDragOver={(e) => hasFiles(e) && e.preventDefault()}
      onDragLeave={() => setDragDepth((d) => Math.max(0, d - 1))}
      onDrop={onDrop}
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
        <h2 className="text-sm font-semibold">Create</h2>
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8" onClick={clear} aria-label="Clear the form">
              <X className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Clear the prompt and references</TooltipContent>
        </Tooltip>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
        {/* Mode comes first: it decides what the block under it asks for. */}
        <div className="grid gap-2">
          <Label htmlFor="compose-mode">Mode</Label>
          <Select value={s.mode} onValueChange={(v) => s.setMode(v as Mode)}>
            <SelectTrigger id="compose-mode" className="h-9 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COMPOSE_MODES.map((m) => (
                <SelectItem key={m} value={m}>
                  {MODE_LABEL[m]}
                  {MODE_HINT[m] ? <span className="text-muted-foreground"> · {MODE_HINT[m]}</span> : null}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {s.mode === 'flf2v' ? (
          <div className="grid gap-2">
            <Label>Start and end frames</Label>
            <div className="flex items-start gap-2">
              <FrameSlot slot="start" label="Start frame" />
              <ArrowRight className="mt-7 size-4 shrink-0 text-muted-foreground" />
              <FrameSlot slot="end" label="End frame" />
            </div>
            <p className="text-xs text-muted-foreground">
              Both are needed. The clip moves from the first frame to the second.
            </p>
            {s.refs.length ? (
              <p className="text-xs text-faint">
                {s.refs.length} reference{s.refs.length === 1 ? '' : 's'} kept for Reference mode.
              </p>
            ) : null}
          </div>
        ) : s.mode === 'extend' ? (
          <ExtendSlot />
        ) : (
          <div className="grid gap-2">
            <Label>References</Label>
            <RefTiles onPick={() => fileInput.current?.click()} />
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              accept="image/*,.zip,.json,.txt"
              onChange={(e) => {
                if (e.target.files) handleFiles(e.target.files)
                e.target.value = ''
              }}
            />
            {s.mode === 't2v' && s.refs.length ? (
              <p className="text-xs text-muted-foreground">
                Text → video does not use these. They are kept for when you switch to Reference.
              </p>
            ) : null}
          </div>
        )}

        {/* Grows into spare height but never shrinks below its content; a short panel scrolls instead. */}
        <div className="flex flex-[1_0_auto] flex-col gap-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="compose-prompt">Prompt</Label>
            <div className="flex-1" />
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={s.split}
              onValueChange={(v) => v && s.setSplit(v as 'single' | 'lines')}
              aria-label="How to read the prompt box"
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="single" className="px-2.5 text-xs">One prompt</ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>The whole box is one prompt, line breaks included</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="lines" className="px-2.5 text-xs">Line = clip</ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>Every line becomes its own clip</TooltipContent>
              </Tooltip>
            </ToggleGroup>
          </div>
          <Textarea
            id="compose-prompt"
            value={s.prompt}
            onChange={(e) => s.setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                submit()
              }
            }}
            placeholder={
              s.split === 'lines'
                ? 'One prompt per line. Each line becomes its own clip.'
                : 'Describe the shot. Line breaks are fine.'
            }
            className="min-h-40 flex-1 resize-none text-base leading-relaxed"
          />
          {s.split === 'lines' && s.prompt.trim() ? (
            <p className="text-xs text-muted-foreground">
              {clipCount(s.prompt, 'lines', 1)} {clipCount(s.prompt, 'lines', 1) === 1 ? 'line' : 'lines'} → {count}{' '}
              {count === 1 ? 'clip' : 'clips'}
            </p>
          ) : null}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-2">
            <Label>Seconds</Label>
            <NumberStepper label="Seconds" value={s.seconds} min={SECONDS.min} max={SECONDS.max} onChange={s.setSeconds} />
          </div>
          <div className="grid gap-2">
            <Label>Takes</Label>
            <NumberStepper label="Takes" value={s.takes} min={TAKES.min} max={TAKES.max} onChange={s.setTakes} />
          </div>
          <div className="col-span-2 grid gap-2">
            <Label htmlFor="compose-preset">Quality</Label>
            <Select value={s.preset} onValueChange={s.setPreset}>
              <SelectTrigger id="compose-preset" className="h-9 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(config?.presets ?? { [s.preset]: null }).map(([key, p]) => (
                  <SelectItem key={key} value={key}>
                    {presetLabel(key)}
                    {p ? <span className="text-muted-foreground"> · {presetSize(p)}</span> : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="col-span-2 flex items-center justify-between gap-4 rounded-md border px-3 py-2">
            <div className="grid gap-0.5">
              <Label htmlFor="compose-audio">Sound</Label>
              <p className="text-2xs text-muted-foreground">
                {s.keepAudio && s.preset === 'turbo'
                  ? 'Turbo renders sound as noise. Final or HD 720p make it usable.'
                  : 'H3 makes its own audio: room tone, effects, even voices.'}
              </p>
            </div>
            <Switch
              id="compose-audio"
              checked={s.keepAudio}
              onCheckedChange={s.setKeepAudio}
              aria-label="Sound"
            />
          </div>
        </div>

      </div>

      <footer className="shrink-0 border-t p-4">
        {/* The cost sits beside the button that spends it, never scrolled out of view. */}
        <div className="mb-3 empty:hidden">
          <EstimateLine body={estimateBody} />
        </div>
        <Button className="h-10 w-full gap-2 text-sm" disabled={block !== null || add.isPending} onClick={submit}>
          {add.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          {(block && BLOCK_LABEL[block]) ?? (count > 1 ? `Add ${count} clips to the queue` : 'Add to the queue')}
        </Button>
        <p className="mt-2 text-center text-2xs text-faint">
          <kbd className="font-sans">⌘</kbd>/<kbd className="font-sans">Ctrl</kbd> + <kbd className="font-sans">Enter</kbd> also adds it
        </p>
      </footer>

      {dragDepth > 0 ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl border-2 border-dashed border-primary bg-background/80">
          <span className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">
            Drop images, a .zip, or a batch
          </span>
        </div>
      ) : null}
    </section>
  )
}
