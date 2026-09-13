import { ArrowRight, AudioLines, ChevronDown, Film, ImagePlus, Loader2, Plus, RotateCw, Sparkles, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { useAddJobs } from '@/api/mutations'
import { useEstimate, useStatus } from '@/api/queries'
import type { Mode, NewJobsBody, Preset } from '@/api/types'
import { NumberStepper } from '@/components/app/NumberStepper'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
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
import { SECONDS, TAKES, blockedBy, clipCount, draftOf, tilesUsedBy, toPayload, useCompose, DEFAULT_SHIFT, SHIFT, SIZE, STEPS, controlsError, controlsPayload, MAX_SHOTS, MIN_SECONDS_PER_SHOT, newShot, promptText, type Shot, type Split, type Transition, MAX_KEYFRAMES, keyframesError } from './composeStore'
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
  controls: 'Fix the advanced controls',
  keyframes: 'Fix the keyframe times',
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

      <div className="mt-1 grid gap-2 border-t pt-3">
        <Label>Where it arrives (optional)</Label>
        <div className="flex items-start gap-3">
          <div className="w-36 shrink-0">
            <FrameSlot slot="end" label="End frame" />
          </div>
          <p className="flex-1 text-xs text-muted-foreground">
            Leave this empty and the clip carries on wherever the prompt takes it. Add a picture
            and it continues the clip <em>and</em> arrives at that picture — the only way to say
            where an extension should end up.
          </p>
        </div>
      </div>
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

  const text = promptText(s)
  const count = clipCount(text, s.split, s.takes)
  const block = blockedBy(s)
  const usedKeys = tilesUsedBy(s).map((t) => t.key ?? t.id).join('|')

  const payload = useMemo(
    () => toPayload(s),
    // usedKeys stands in for the tiles, which are new objects on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [s.prompt, s.split, s.shots, s.seconds, s.preset, s.mode, s.takes, s.keepAudio, s.sound, s.music, s.steps, s.shiftVideo, s.shiftAudio, s.width, s.height, s.seed, s.keyframes, usedKeys],
  )

  // Priced fields only. The server ignores images when estimating, so the price
  // must not be re-fetched every time one finishes uploading.
  const estimateBody = useMemo<NewJobsBody>(
    () => ({
      prompts: text, split: s.split === 'shots' ? 'single' : s.split, seconds: s.seconds, preset: s.preset, mode: s.mode, count: s.takes,
      ...controlsPayload({ steps: s.steps, shiftVideo: s.shiftVideo, shiftAudio: s.shiftAudio, width: s.width, height: s.height, seed: s.seed }, s.takes),
    }),
    [text, s.split, s.seconds, s.preset, s.mode, s.takes, s.steps, s.shiftVideo, s.shiftAudio, s.width, s.height, s.seed],
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
              onValueChange={(v) => v && s.setSplit(v as Split)}
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
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="shots" className="px-2.5 text-xs">Shots</ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>Several shots with cuts inside one clip</TooltipContent>
              </Tooltip>
            </ToggleGroup>
          </div>
          {s.split === 'shots' ? (
            <ShotsEditor />
          ) : (
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
          )}
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
                {Object.entries(config?.presets ?? { [s.preset]: null }).filter(([, p]) => !p?.hidden).map(([key, p]) => (
                  <SelectItem key={key} value={key}>
                    {presetLabel(key)}
                    {p ? <span className="text-muted-foreground"> · {presetSize(p)}</span> : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {s.mode !== 'extend' ? <AudioSlot /> : null}

          <KeyframesSection />

          <AdvancedControls preset={config?.presets[s.preset]} />

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

          {s.keepAudio ? (
            <div className="col-span-2 grid gap-2 rounded-md border px-3 py-2">
              <div className="grid gap-0.5">
                <span className="text-sm font-medium">Sound direction</span>
                <p className="text-2xs text-muted-foreground">
                  Optional. Kept apart from the shot: H3 reads a soundscape and a music line far better than
                  sound words inside the prompt.{' '}
                  <Link to="/beta#sound" className="underline underline-offset-2">
                    How it works
                  </Link>
                </p>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="compose-sound" className="text-2xs text-muted-foreground">
                  Soundscape
                </Label>
                <Input
                  id="compose-sound"
                  value={s.sound}
                  onChange={(e) => s.setSound(e.target.value)}
                  placeholder="wind in olive leaves, distant birds, a lantern creaking"
                  maxLength={1000}
                />
              </div>
              <div className="grid gap-1">
                <Label htmlFor="compose-music" className="text-2xs text-muted-foreground">
                  Music
                </Label>
                <Input
                  id="compose-music"
                  value={s.music}
                  onChange={(e) => s.setMusic(e.target.value)}
                  placeholder="slow solo piano, warm and hopeful, no vocals"
                  maxLength={1000}
                />
              </div>
            </div>
          ) : null}
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


/** Every knob the graph has, on top of the chosen preset. Collapsed by default:
 *  a clip that never opens this renders exactly as the preset says. */
function AdvancedControls({ preset }: { preset: Preset | undefined }) {
  const s = useCompose()
  const [open, setOpen] = useState(
    () => s.steps !== null || s.shiftVideo !== null || s.shiftAudio !== null || s.width !== null || s.seed !== null,
  )
  const error = controlsError(s)
  const sizeMode = s.width === null ? 'preset' : s.width === 1920 && s.height === 1088 ? 'hd1080' : 'custom'
  const number = (raw: string, integer: boolean): number | null => {
    if (raw.trim() === '') return null
    const n = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw)
    return Number.isFinite(n) ? n : null
  }
  return (
    <div className="col-span-2 rounded-md border">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm font-medium"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>
          Advanced controls
          {!open && error ? <span className="ml-2 text-xs text-destructive">{error}</span> : null}
        </span>
        <ChevronDown className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-180')} />
      </button>
      {open ? (
        <div className="grid gap-3 border-t px-3 py-3">
          <p className="text-2xs text-muted-foreground">
            Blank = the preset's own value. Everything here rides along with "Use again".{' '}
            <Link to="/beta#controls" className="underline underline-offset-2">
              What each one does
            </Link>
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1">
              <Label htmlFor="ctl-steps" className="text-2xs text-muted-foreground">
                Steps · {STEPS.min}–{STEPS.max}{preset ? ` · preset ${preset.steps}` : ''}
              </Label>
              <Input
                id="ctl-steps"
                inputMode="numeric"
                placeholder={preset ? String(preset.steps) : ''}
                value={s.steps ?? ''}
                onChange={(e) => s.setControls({ steps: number(e.target.value, true) })}
              />
              {s.preset === 'turbo' && s.steps !== null ? (
                <p className="text-2xs text-warn">Turbo is distilled for 4 steps; more only costs time.</p>
              ) : null}
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ctl-seed" className="text-2xs text-muted-foreground">
                Seed · blank = random
              </Label>
              <Input
                id="ctl-seed"
                inputMode="numeric"
                placeholder="random"
                value={s.seed ?? ''}
                onChange={(e) => s.setControls({ seed: number(e.target.value, true) })}
              />
              {s.seed !== null && s.takes > 1 ? (
                <p className="text-2xs text-muted-foreground">Ignored for several takes: they would all be the same clip.</p>
              ) : null}
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ctl-motion" className="text-2xs text-muted-foreground">
                Motion (video shift) · default {DEFAULT_SHIFT.video}
              </Label>
              <Input
                id="ctl-motion"
                inputMode="decimal"
                placeholder={String(DEFAULT_SHIFT.video)}
                value={s.shiftVideo ?? ''}
                onChange={(e) => s.setControls({ shiftVideo: number(e.target.value, false) })}
              />
              <p className="text-2xs text-muted-foreground">
                {SHIFT.min}–{SHIFT.max}. Lower keeps the picture closer to the frame; higher lets it move and change more.
              </p>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ctl-audio-shift" className="text-2xs text-muted-foreground">
                Audio shift · default {DEFAULT_SHIFT.audio}
              </Label>
              <Input
                id="ctl-audio-shift"
                inputMode="decimal"
                placeholder={String(DEFAULT_SHIFT.audio)}
                value={s.shiftAudio ?? ''}
                onChange={(e) => s.setControls({ shiftAudio: number(e.target.value, false) })}
              />
            </div>
            <div className="col-span-2 grid gap-1">
              <Label htmlFor="ctl-size" className="text-2xs text-muted-foreground">
                Render size
              </Label>
              <Select
                value={sizeMode}
                onValueChange={(v) =>
                  s.setControls(
                    v === 'preset'
                      ? { width: null, height: null }
                      : v === 'hd1080'
                        ? { width: 1920, height: 1088 }
                        : { width: s.width ?? preset?.width ?? 1344, height: s.height ?? preset?.height ?? 768 },
                  )
                }
              >
                <SelectTrigger id="ctl-size" className="h-9 w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="preset">Preset size{preset ? ` · ${preset.width}×${preset.height}` : ''}</SelectItem>
                  <SelectItem value="hd1080">1920×1088 · experimental</SelectItem>
                  <SelectItem value="custom">Custom</SelectItem>
                </SelectContent>
              </Select>
              {sizeMode === 'custom' ? (
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    aria-label="Width"
                    inputMode="numeric"
                    value={s.width ?? ''}
                    onChange={(e) => s.setControls({ width: number(e.target.value, true) })}
                  />
                  <Input
                    aria-label="Height"
                    inputMode="numeric"
                    value={s.height ?? ''}
                    onChange={(e) => s.setControls({ height: number(e.target.value, true) })}
                  />
                </div>
              ) : null}
              {sizeMode !== 'preset' ? (
                <p className="text-2xs text-warn">
                  Beyond the trained canvas (768 px short edge): about {Math.round(((s.width ?? 0) * (s.height ?? 0)) / (1344 * 768) * 10) / 10}× the render time, and untested.
                </p>
              ) : (
                <p className="text-2xs text-muted-foreground">Multiples of {SIZE.multiple}, up to 1920×1088.</p>
              )}
            </div>
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
      ) : null}
    </div>
  )
}


const TRANSITIONS: { value: Transition; label: string }[] = [
  { value: 'cut', label: 'Cut to' },
  { value: 'match', label: 'Match cut to' },
  { value: 'continuous', label: 'Same take, camera moves on' },
]

/** Several shots inside one clip. The model reads "SHOT 1 … SHOT 2" and edits
 *  inside the render: one world, one colour, one soundtrack across the cuts. */
function ShotsEditor() {
  const s = useCompose()
  const shots = s.shots
  const update = (id: string, patch: Partial<Shot>) =>
    s.setShots(shots.map((sh) => (sh.id === id ? { ...sh, ...patch } : sh)))
  const remove = (id: string) => s.setShots(shots.filter((sh) => sh.id !== id))
  const filled = shots.filter((sh) => sh.text.trim()).length
  const perShot = filled ? s.seconds / filled : s.seconds
  return (
    <div className="grid gap-2">
      {shots.map((shot, i) => (
        <div key={shot.id} className="grid gap-1.5 rounded-md border p-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">Shot {i + 1}</span>
            {i > 0 ? (
              <Select value={shot.transition} onValueChange={(v) => update(shot.id, { transition: v as Transition })}>
                <SelectTrigger aria-label={`Shot ${i + 1} transition`} className="h-7 w-auto min-w-28 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRANSITIONS.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <span className="text-2xs text-muted-foreground">opens on the reference</span>
            )}
            <div className="flex-1" />
            {shots.length > 1 ? (
              <Button size="icon" variant="ghost" className="size-7" aria-label={`Remove shot ${i + 1}`} onClick={() => remove(shot.id)}>
                <Trash2 className="size-3.5" />
              </Button>
            ) : null}
          </div>
          <Textarea
            aria-label={`Shot ${i + 1}`}
            value={shot.text}
            onChange={(e) => update(shot.id, { text: e.target.value })}
            placeholder={i === 0 ? 'the scene as it opens; what moves, where the camera goes' : 'what this shot shows'}
            rows={2}
            className="min-h-16 resize-none text-sm leading-relaxed"
          />
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={shots.length >= MAX_SHOTS} onClick={() => s.setShots([...shots, newShot()])}>
          <Plus /> Add shot
        </Button>
        <p className="text-2xs text-muted-foreground">
          {filled ? `${filled} shot${filled === 1 ? '' : 's'} in ${s.seconds}s ≈ ${perShot.toFixed(1)}s each` : 'Up to 6 shots in one clip.'}
          {filled > 1 && perShot < MIN_SECONDS_PER_SHOT ? ' — that is rushed; add seconds or drop a shot.' : ''}
          {' '}
          <Link to="/beta#shots" className="underline underline-offset-2">
            How it works
          </Link>
        </p>
      </div>
    </div>
  )
}


/** Images pinned at a moment inside the clip. The start and end frames have
 *  their own slots; these anchor everything in between. */
function KeyframesSection() {
  const s = useCompose()
  const { handleFiles } = useReferenceUploads()
  const input = useRef<HTMLInputElement>(null)
  const error = keyframesError(s)
  const number = (raw: string): number | null => {
    const n = Number.parseFloat(raw)
    return Number.isFinite(n) ? n : null
  }
  return (
    <div className="col-span-2 grid gap-2 rounded-md border px-3 py-2">
      <div className="flex items-center gap-2">
        <div className="grid gap-0.5">
          <span className="text-sm font-medium">Keyframes</span>
          <p className="text-2xs text-muted-foreground">
            Pin an image at any second of the clip.{' '}
            <Link to="/beta#keyframes" className="underline underline-offset-2">
              How it works
            </Link>
          </p>
        </div>
        <div className="flex-1" />
        <input
          ref={input}
          type="file"
          multiple
          hidden
          accept="image/*"
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files, 'keyframe')
            e.target.value = ''
          }}
        />
        <Button size="sm" variant="outline" disabled={s.keyframes.length >= MAX_KEYFRAMES} onClick={() => input.current?.click()}>
          <ImagePlus /> Add keyframe
        </Button>
      </div>
      {s.keyframes.length ? (
        <ul className="grid gap-2">
          {s.keyframes.map((k, i) => (
            <li key={k.tile.id} className="flex items-center gap-3">
              <ImageTile tile={k.tile} className="aspect-video w-24 shrink-0" />
              <Label htmlFor={`keyframe-at-${k.tile.id}`} className="text-xs text-muted-foreground">
                Keyframe {i + 1} at
              </Label>
              <Input
                id={`keyframe-at-${k.tile.id}`}
                inputMode="decimal"
                className="h-8 w-20"
                value={k.at}
                onChange={(e) => {
                  const at = number(e.target.value)
                  if (at !== null) s.setKeyframeAt(k.tile.id, at)
                }}
              />
              <span className="text-xs text-muted-foreground">s of {s.seconds}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  )
}


/** A voice or audio track the clip follows: mouth, timing and expression sync to it. */
function AudioSlot() {
  const audio = useCompose((s) => s.audio)
  const { handleFiles, remove, retry, canRetry } = useReferenceUploads()
  const input = useRef<HTMLInputElement>(null)
  return (
    <div className="col-span-2 grid gap-2 rounded-md border px-3 py-2">
      <div className="flex items-center gap-2">
        <div className="grid gap-0.5">
          <span className="text-sm font-medium">Voice / audio track</span>
          <p className="text-2xs text-muted-foreground">
            Optional. The clip follows it - lip-sync in any language, since the words are yours.{' '}
            <Link to="/beta#lipsync" className="underline underline-offset-2">
              How it works
            </Link>
          </p>
        </div>
        <div className="flex-1" />
        <input
          ref={input}
          type="file"
          hidden
          accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac"
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files)
            e.target.value = ''
          }}
        />
        {!audio ? (
          <Button size="sm" variant="outline" onClick={() => input.current?.click()}>
            <AudioLines /> Add audio
          </Button>
        ) : null}
      </div>
      {audio ? (
        <div className="flex items-center gap-3 rounded-md bg-field p-2">
          <AudioLines className="size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{audio.name}</p>
            <p className={cn('text-2xs', audio.status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
              {audio.status === 'error'
                ? (audio.error ?? 'Upload failed')
                : audio.status === 'uploading'
                  ? `Uploading… ${Math.round(audio.progress * 100)}%`
                  : 'Anchored at the start; trimmed to the clip length. Sound stays on.'}
            </p>
          </div>
          {audio.status === 'error' && canRetry(audio.id) ? (
            <Button size="sm" variant="ghost" onClick={() => retry(audio.id)}>
              Retry
            </Button>
          ) : null}
          <Button size="icon" variant="ghost" className="size-8" aria-label="Remove the audio track" onClick={() => remove(audio.id)}>
            <X className="size-4" />
          </Button>
        </div>
      ) : null}
    </div>
  )
}
