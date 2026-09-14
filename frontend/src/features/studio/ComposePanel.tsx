import { ArrowRight, AudioLines, Check, ChevronDown, Film, ImagePlus, Loader2, Plus, RotateCw, Sparkles, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router'
import { Select as SelectPrimitive } from 'radix-ui'
import { toast } from 'sonner'

import { useAddJobs, useImprovePrompt } from '@/api/mutations'
import { useEstimate, useStatus } from '@/api/queries'
import type { Mode, NewJobsBody, Preset, PublicConfig } from '@/api/types'
import { NumberStepper } from '@/components/app/NumberStepper'
import { RefThumb } from '@/components/app/RefThumb'
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
import { tn, useT, type Key } from '@/i18n'
import { fmtDuration, fmtUsd, modeLabel, presetLabel } from '@/lib/format'
import { useDebouncedValue } from '@/lib/hooks'
import { imageUrl } from '@/lib/media'
import { COMPOSE_MODES } from '@/lib/modes'
import { cn } from '@/lib/utils'

import type { Block, RefTile } from './composeStore'
import { SECONDS, TAKES, blockedBy, clipCount, draftOf, tilesUsedBy, toPayload, useCompose, DEFAULT_SHIFT, SHIFT, STEPS, controlsError, controlsPayload, MAX_SHOTS, MIN_SECONDS_PER_SHOT, newShot, promptText, type Shot, type Split, type Transition, MAX_KEYFRAMES, keyframesError, MAX_REF_IMAGES, MAX_REF_MEDIA, EFFECTS, MAX_EFFECTS } from './composeStore'
import { ExtendSourceDialog } from './ExtendSourceDialog'
import { useFilePaste, useReferenceUploads } from './useReferenceUploads'

// What each mode asks for, said in the picker so the block below is never a surprise.
const MODE_HINT: Partial<Record<Mode, Key>> = {
  i2v: 'compose.hint.i2v',
  t2v: 'compose.hint.t2v',
  flf2v: 'compose.hint.flf2v',
  extend: 'compose.hint.extend',
  r2v: 'compose.hint.r2v',
}

// Why the button is off. 'prompt' is deliberately absent: an empty prompt box
// already says so itself, and the button keeps its usual name.
const BLOCK_LABEL: Partial<Record<Block, Key>> = {
  uploading: 'compose.block.uploading',
  'upload-failed': 'compose.block.uploadFailed',
  'start-frame': 'compose.block.startFrame',
  'end-frame': 'compose.block.endFrame',
  'extend-source': 'compose.block.extendSource',
  controls: 'compose.block.controls',
  keyframes: 'compose.block.keyframes',
  references: 'compose.block.references',
}

/** One picked image, wherever it sits: a reference, a start frame, an end frame. */
export function ImageTile({ tile, className }: { tile: RefTile; className?: string }) {
  const t = useT()
  const { retry, remove, canRetry } = useReferenceUploads()
  return (
    <div className={cn('group relative overflow-hidden rounded-md border bg-field', className)} title={tile.name}>
      <img
        src={tile.previewUrl ?? (tile.key ? imageUrl(tile.key) : undefined)}
        alt={tile.name}
        className={cn('size-full object-cover', tile.status !== 'ready' && 'opacity-50')}
      />
      {tile.status === 'uploading' ? (
        <div className="absolute inset-x-1 bottom-1 h-1 overflow-hidden rounded-full bg-black/60" aria-label={t('compose.uploading', { name: tile.name })}>
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
              aria-label={t('compose.retryUpload', { name: tile.name })}
            >
              <RotateCw className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{tile.error ?? t('common.uploadFailed')} {t('compose.clickRetry')}</TooltipContent>
        </Tooltip>
      ) : null}
      <button
        type="button"
        onClick={() => remove(tile.id)}
        aria-label={t('compose.removeTile', { name: tile.name })}
        className="absolute top-1 end-1 grid size-5 place-items-center rounded-full bg-black/70 text-white opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
      >
        <X className="size-3" />
      </button>
    </div>
  )
}

/** One of the two frames Start to end needs, with its own picker. */
export function FrameSlot({ slot, label }: { slot: 'start' | 'end'; label: string }) {
  const t = useT()
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
          aria-label={t('compose.addFrame', { label: label.toLowerCase() })}
        >
          <ImagePlus className="size-5" />
        </button>
      )}
      <p className="mt-1 text-center text-2xs text-muted-foreground">{label}</p>
    </div>
  )
}

/** The clip an extension continues: one from the Archive, or one uploaded. */
export function ExtendSlot() {
  const t = useT()
  const source = useCompose((s) => s.extendSource)
  const { handleFiles, remove, retry, canRetry } = useReferenceUploads()
  const input = useRef<HTMLInputElement>(null)
  const [picking, setPicking] = useState(false)

  return (
    <div className="grid gap-2">
      <Label>{t('compose.videoToContinue')}</Label>
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
                ? (source.tile.error ?? t('common.uploadFailed'))
                : source.tile.status === 'uploading'
                  ? t('compose.uploadingShort')
                  : source.from === 'clip'
                    ? t('compose.fromArchive')
                    : t('compose.uploadedVideo')}
            </p>
          </div>
          {source.tile.status === 'error' && canRetry(source.tile.id) ? (
            <Button size="sm" variant="ghost" onClick={() => retry(source.tile.id)}>
              {t('common.retry')}
            </Button>
          ) : null}
          <Button
            size="icon"
            variant="ghost"
            className="size-8"
            aria-label={t('compose.removeSource')}
            onClick={() => remove(source.tile.id)}
          >
            <X className="size-4" />
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button variant="outline" className="flex-1 gap-2" onClick={() => setPicking(true)}>
            <Film className="size-4" /> {t('compose.fromClips')}
          </Button>
          <Button variant="outline" className="flex-1 gap-2" onClick={() => input.current?.click()}>
            <Upload className="size-4" /> {t('compose.uploadVideo')}
          </Button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t('compose.extendHelp')}</p>

      <div className="mt-1 grid gap-2 border-t pt-3">
        <Label>{t('compose.arrives')}</Label>
        <div className="flex items-start gap-3">
          <div className="w-36 shrink-0">
            <FrameSlot slot="end" label={t('compose.endFrame')} />
          </div>
          <p className="flex-1 text-xs text-muted-foreground">{t('compose.arrivesHelp')}</p>
        </div>
      </div>
      <ExtendSourceDialog open={picking} onOpenChange={setPicking} />
    </div>
  )
}

export function RefTiles({ onPick }: { onPick: () => void }) {
  const t = useT()
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
            aria-label={t('compose.addRefs')}
          >
            <ImagePlus className="size-5" />
          </button>
        </TooltipTrigger>
        <TooltipContent>{t('compose.refsTip')}</TooltipContent>
      </Tooltip>
    </div>
  )
}

export function EstimateLine({ body }: { body: NewJobsBody }) {
  const t = useT()
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
        <b className="text-foreground tabular-nums">{e.clips}</b> {t(e.clips === 1 ? 'compose.clipOne' : 'compose.clipMany')}
      </span>
      <span>
        {t('compose.about')}{' '}
        <b className="text-foreground tabular-nums">
          {e.total_minutes >= 1 ? t('compose.minutes', { n: Math.round(e.total_minutes) }) : fmtDuration(e.total_minutes * 60)}
        </b>
      </span>
      <span>
        <b className="text-foreground tabular-nums">{fmtUsd(e.cost_usd)}</b> {t('compose.each', { cost: fmtUsd(e.cost_per_clip_usd, 3) })}
      </span>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="outline" className={cn('ms-auto', e.confidence === 'measured' ? 'border-ok/40 text-ok' : 'text-muted-foreground')}>
            {t(e.confidence === 'measured' ? 'compose.measured' : 'compose.estimated')}
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-64">
          {t(e.confidence === 'measured' ? 'compose.measuredTip' : 'compose.estimatedTip')}
        </TooltipContent>
      </Tooltip>
    </div>
  )
}

export function ComposePanel() {
  const t = useT()
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
  const blockKey = block ? BLOCK_LABEL[block] : undefined
  const usedKeys = tilesUsedBy(s).map((tile) => tile.key ?? tile.id).join('|')

  const payload = useMemo(
    () => toPayload(s),
    // usedKeys stands in for the tiles, which are new objects on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [s.prompt, s.split, s.shots, s.seconds, s.preset, s.mode, s.takes, s.keepAudio, s.sound, s.music, s.effects, s.steps, s.shiftVideo, s.shiftAudio, s.width, s.height, s.seed, s.keyframes, usedKeys],
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
    toast(t('compose.formCleared'), { action: { label: t('common.undo'), onClick: () => useCompose.getState().restore(snapshot) } })
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragDepth(0)
    if (event.dataTransfer.files.length) handleFiles(event.dataTransfer.files)
  }
  const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')

  return (
    <section
      aria-label={t('compose.title')}
      className="relative flex min-h-0 flex-1 flex-col rounded-xl border bg-card"
      onDragEnter={(e) => hasFiles(e) && setDragDepth((d) => d + 1)}
      onDragOver={(e) => hasFiles(e) && e.preventDefault()}
      onDragLeave={() => setDragDepth((d) => Math.max(0, d - 1))}
      onDrop={onDrop}
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
        <h2 className="text-sm font-semibold">{t('compose.title')}</h2>
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8" onClick={clear} aria-label={t('compose.clearForm')}>
              <X className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t('compose.clearTip')}</TooltipContent>
        </Tooltip>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-x-hidden overflow-y-auto p-4">
        {/* Mode comes first: it decides what the block under it asks for. */}
        <div className="grid gap-2">
          <Label htmlFor="compose-mode">{t('compose.mode')}</Label>
          <Select value={s.mode} onValueChange={(v) => s.setMode(v as Mode)}>
            <SelectTrigger id="compose-mode" className="h-9 w-full min-w-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COMPOSE_MODES.map((m) => {
                const hint = MODE_HINT[m]
                // The hint lives only in the list: the closed picker shows the name alone,
                // so it never grows wider than the panel.
                return (
                  <SelectPrimitive.Item key={m} value={m} className={MODE_ITEM_CLASS}>
                    <span className="absolute end-2 flex size-3.5 items-center justify-center">
                      <SelectPrimitive.ItemIndicator>
                        <Check className="size-4" />
                      </SelectPrimitive.ItemIndicator>
                    </span>
                    <span className="grid gap-0.5">
                      <SelectPrimitive.ItemText>{modeLabel(m)}</SelectPrimitive.ItemText>
                      {hint ? <span className="text-2xs text-muted-foreground">{t(hint)}</span> : null}
                    </span>
                  </SelectPrimitive.Item>
                )
              })}
            </SelectContent>
          </Select>
        </div>

        {s.mode === 'flf2v' ? (
          <div className="grid gap-2">
            <Label>{t('compose.frames')}</Label>
            <div className="flex items-start gap-2">
              <FrameSlot slot="start" label={t('compose.startFrame')} />
              <ArrowRight className="mt-7 size-4 shrink-0 text-muted-foreground rtl:rotate-180" />
              <FrameSlot slot="end" label={t('compose.endFrame')} />
            </div>
            <p className="text-xs text-muted-foreground">{t('compose.framesHelp')}</p>
            {s.refs.length ? (
              <p className="text-xs text-faint">{tn('compose.refsKeptOne', 'compose.refsKeptMany', s.refs.length)}</p>
            ) : null}
          </div>
        ) : s.mode === 'extend' ? (
          <ExtendSlot />
        ) : s.mode === 'r2v' ? (
          <ReferencesBlock onPickImages={() => fileInput.current?.click()} />
        ) : (
          <div className="grid gap-2">
            <Label>{t('compose.references')}</Label>
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
              <p className="text-xs text-muted-foreground">{t('compose.t2vRefs')}</p>
            ) : null}
          </div>
        )}

        {/* Grows into spare height but never shrinks below its content; a short panel scrolls instead. */}
        <div className="flex flex-[1_0_auto] flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor="compose-prompt" className="whitespace-nowrap">{t('compose.prompt')}</Label>
            {config?.prompt_helper ? <ImproveButton /> : null}
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={s.split}
              onValueChange={(v) => v && s.setSplit(v as Split)}
              aria-label={t('compose.splitAria')}
              className="ms-auto max-w-full flex-wrap"
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="single" className="px-2.5 text-xs">{t('compose.onePrompt')}</ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>{t('compose.onePromptTip')}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="lines" className="px-2.5 text-xs">{t('compose.lineClip')}</ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>{t('compose.lineClipTip')}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="shots" className="px-2.5 text-xs">{t('compose.shots')}</ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>{t('compose.shotsTip')}</TooltipContent>
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
                ? t('compose.placeholderLines')
                : t('compose.placeholder')
            }
            className="min-h-40 flex-1 resize-none text-base leading-relaxed"
          />
          )}
          {s.split === 'lines' && s.prompt.trim() ? (
            <p className="text-xs text-muted-foreground">
              {clipCount(s.prompt, 'lines', 1)} {t(clipCount(s.prompt, 'lines', 1) === 1 ? 'compose.lineOne' : 'compose.lineMany')} → {count}{' '}
              {t(count === 1 ? 'compose.clipOne' : 'compose.clipMany')}
            </p>
          ) : null}
          <EffectsPicker />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-2">
            <Label>{t('compose.seconds')}</Label>
            <NumberStepper label={t('compose.seconds')} value={s.seconds} min={SECONDS.min} max={SECONDS.max} onChange={s.setSeconds} />
          </div>
          <div className="grid gap-2">
            <Label>{t('compose.takes')}</Label>
            <NumberStepper label={t('compose.takes')} value={s.takes} min={TAKES.min} max={TAKES.max} onChange={s.setTakes} />
          </div>
          <QualityPicker config={config} />

          {s.mode !== 'extend' ? <AudioSlot /> : null}

          <KeyframesSection />

          <FineTuning preset={config?.presets[s.preset]} />

          <div className="col-span-2 flex items-center justify-between gap-4 rounded-md border px-3 py-2">
            <div className="grid gap-0.5">
              <Label htmlFor="compose-audio">{t('compose.sound')}</Label>
              <p className="text-2xs text-muted-foreground">
                {t(s.keepAudio && s.preset === 'turbo' ? 'compose.soundTurbo' : 'compose.soundHelp')}
              </p>
            </div>
            <Switch
              id="compose-audio"
              checked={s.keepAudio}
              onCheckedChange={s.setKeepAudio}
              aria-label={t('compose.sound')}
            />
          </div>

          {s.keepAudio ? (
            <div className="col-span-2 grid gap-2 rounded-md border px-3 py-2">
              <div className="grid gap-0.5">
                <span className="text-sm font-medium">{t('compose.soundDirection')}</span>
                <p className="text-2xs text-muted-foreground">
                  {t('compose.soundDirectionHelp')}{' '}
                  <Link to="/beta#sound" className="underline underline-offset-2">
                    {t('compose.howItWorks')}
                  </Link>
                </p>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="compose-sound" className="text-2xs text-muted-foreground">
                  {t('compose.soundscape')}
                </Label>
                <Input
                  id="compose-sound"
                  value={s.sound}
                  onChange={(e) => s.setSound(e.target.value)}
                  placeholder={t('compose.soundscapePh')}
                  maxLength={1000}
                />
              </div>
              <div className="grid gap-1">
                <Label htmlFor="compose-music" className="text-2xs text-muted-foreground">
                  {t('compose.music')}
                </Label>
                <Input
                  id="compose-music"
                  value={s.music}
                  onChange={(e) => s.setMusic(e.target.value)}
                  placeholder={t('compose.musicPh')}
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
          {blockKey ? t(blockKey) : count > 1 ? t('compose.addN', { n: count }) : t('compose.addQueue')}
        </Button>
        <p className="mt-2 text-center text-2xs text-faint">
          <kbd className="font-sans">⌘</kbd>/<kbd className="font-sans">Ctrl</kbd> + <kbd className="font-sans">Enter</kbd> {t('compose.alsoAdds')}
        </p>
      </footer>

      {dragDepth > 0 ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl border-2 border-dashed border-primary bg-background/80">
          <span className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">
            {t('compose.drop')}
          </span>
        </div>
      ) : null}
    </section>
  )
}


// The list item of the "how to make it" picker, styled like shadcn's SelectItem.
const MODE_ITEM_CLASS =
  'relative flex w-full cursor-default items-center gap-2 rounded-sm py-1.5 pe-8 ps-2 text-sm outline-hidden select-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50'

/** What each speed says about itself. Sizes and times come from the server. */
const SPEED_DESC: Record<string, Key> = { turbo: 'quality.turbo', balanced: 'quality.balanced', final: 'quality.final' }
const SPEED_ORDER = ['turbo', 'balanced', 'final']

/** The model's native canvases: 768 pixels on the short side (576 for 21:9, which
 *  at 768 would be bigger than the model can draw). All multiples of 32. */
const SHAPES: { key: string; w: number; h: number }[] = [
  { key: 'landscape', w: 1344, h: 768 },
  { key: 'portrait', w: 768, h: 1344 },
  { key: 'square', w: 768, h: 768 },
  { key: 'classic', w: 1024, h: 768 },
  { key: 'tall', w: 768, h: 1024 },
  { key: 'wide', w: 1344, h: 576 },
]

/** Speed and shape: every speed is the native size, every shape is one the model was trained on. */
function QualityPicker({ config }: { config: PublicConfig | undefined }) {
  const t = useT()
  const s = useCompose()
  const presets = config?.presets ?? {}
  const speeds = Object.keys(presets)
    .filter((key) => !presets[key]?.hidden)
    .sort((a, b) => (SPEED_ORDER.indexOf(a) + 1 || 99) - (SPEED_ORDER.indexOf(b) + 1 || 99))
  const own = presets[s.preset] ?? { width: 1344, height: 768 }
  const width = s.width ?? own.width
  const height = s.height ?? own.height
  const shape = SHAPES.find((sh) => sh.w === width && sh.h === height)?.key ?? 'custom'
  // The same table the cost line uses: minutes at 10 s for the preset's own size,
  // scaled by the chosen length and by the pixels of the chosen shape.
  const minutesFor = (key: string): number | null => {
    const per10 = config?.estimate.minutes_per_10s[key]
    const p = presets[key]
    if (per10 === undefined || !p) return null
    return ((per10 * Math.max(4, s.seconds)) / 10) * ((width * height) / (p.width * p.height))
  }
  const timeText = (min: number | null) =>
    min === null ? '' : min < 1 ? t('quality.timeShort', { s: s.seconds }) : t('quality.time', { min: Math.round(min), s: s.seconds })
  const pickShape = (key: string) => {
    const sh = SHAPES.find((x) => x.key === key)
    if (!sh) return
    // The preset's own size is "no override", so a clip made this way still says nothing extra.
    s.setControls(sh.w === own.width && sh.h === own.height ? { width: null, height: null } : { width: sh.w, height: sh.h })
  }
  return (
    <div className="col-span-2 grid gap-4">
      <div className="grid gap-2">
        <span id="compose-speed-label" className="text-sm font-medium">{t('quality.speed')}</span>
        <ToggleGroup
          type="single"
          variant="outline"
          spacing={2}
          value={s.preset}
          onValueChange={(v) => v && s.setPreset(v)}
          aria-labelledby="compose-speed-label"
          className="grid w-full"
        >
          {speeds.map((key) => {
            const desc = SPEED_DESC[key]
            return (
              <ToggleGroupItem
                key={key}
                value={key}
                className="h-auto w-full flex-col items-start gap-0.5 px-3 py-2 text-start whitespace-normal data-[state=on]:border-primary data-[state=on]:bg-primary/10"
              >
                <span className="flex w-full flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-medium">{presetLabel(key)}</span>
                  <span className="ms-auto text-xs font-normal text-muted-foreground tabular-nums">{timeText(minutesFor(key))}</span>
                </span>
                {desc ? <span className="text-2xs font-normal text-muted-foreground">{t(desc)}</span> : null}
              </ToggleGroupItem>
            )
          })}
        </ToggleGroup>
      </div>
      <div className="grid gap-2">
        <span id="compose-shape-label" className="text-sm font-medium">{t('quality.shape')}</span>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={shape}
          onValueChange={(v) => v && pickShape(v)}
          aria-labelledby="compose-shape-label"
          className="max-w-full flex-wrap"
        >
          {SHAPES.map((sh) => (
            <ToggleGroupItem key={sh.key} value={sh.key} className="h-auto flex-col items-start gap-0 px-2.5 py-1 text-xs">
              {t(`shape.${sh.key}` as Key)}
              <span className="text-2xs font-normal text-muted-foreground">{sh.w}×{sh.h}</span>
            </ToggleGroupItem>
          ))}
          {shape === 'custom' ? (
            <ToggleGroupItem value="custom" className="h-auto px-2.5 py-1 text-xs">{t('shape.custom', { w: width, h: height })}</ToggleGroupItem>
          ) : null}
        </ToggleGroup>
        <p className="text-2xs text-muted-foreground">
          {t('quality.native')}
          {shape === 'wide' ? ` ${t('shape.wideNote')}` : ''}
        </p>
      </div>
    </div>
  )
}

/** Effect presets: community-trained looks, each one a word the model reads. */
function EffectsPicker() {
  const t = useT()
  const effects = useCompose((s) => s.effects)
  const toggle = useCompose((s) => s.toggleEffect)
  return (
    <div className="grid gap-1.5">
      <span id="compose-effects-label" className="text-xs font-medium">{t('compose.effects')}</span>
      <ToggleGroup
        type="multiple"
        variant="outline"
        size="sm"
        spacing={1}
        value={effects}
        onValueChange={(next) => {
          // One click, one change: the store enforces the cap and the order.
          const added = next.find((n) => !effects.includes(n))
          const removed = effects.find((e) => !next.includes(e))
          const name = added ?? removed
          if (name) toggle(name)
        }}
        aria-labelledby="compose-effects-label"
        className="max-w-full flex-wrap"
      >
        {EFFECTS.map((name) => (
          <ToggleGroupItem
            key={name}
            value={name}
            disabled={!effects.includes(name) && effects.length >= MAX_EFFECTS}
            className="rounded-full px-2.5 text-xs data-[state=on]:border-primary data-[state=on]:bg-primary/10"
          >
            {t(`effect.${name}` as Key)}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <p className="text-2xs text-muted-foreground">{t('compose.effectsHelp', { n: MAX_EFFECTS })}</p>
    </div>
  )
}

/** The prompt helper: the description, the sounds and the music rewritten the official way. */
function ImproveButton() {
  const t = useT()
  const s = useCompose()
  const improve = useImprovePrompt()
  const enabled = s.split === 'single' && s.prompt.trim().length >= 3 && !improve.isPending
  const run = () => {
    const snapshot = draftOf(useCompose.getState())
    improve.mutate(
      {
        prompt: s.prompt, mode: s.mode, seconds: s.seconds,
        ...(s.sound.trim() ? { sound: s.sound.trim() } : {}),
        ...(s.music.trim() ? { music: s.music.trim() } : {}),
        has_start: Boolean(s.mode === 'flf2v' ? s.startFrame : s.refs[0]),
        has_end: Boolean(s.endFrame),
        keyframes: s.keyframes.length,
      },
      {
        onSuccess: (out) => {
          const st = useCompose.getState()
          st.setPrompt(out.description)
          if (out.sounds) st.setSound(out.sounds)
          if (out.music) st.setMusic(out.music)
          if (out.sounds || out.music) st.setKeepAudio(true)
          toast.success(t('compose.improved'), { action: { label: t('common.undo'), onClick: () => useCompose.getState().restore(snapshot) } })
        },
      },
    )
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>
          <Button size="sm" variant="outline" className="h-7 gap-1.5 px-2 text-xs" onClick={run} disabled={!enabled}>
            {improve.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
            {improve.isPending ? t('compose.improving') : t('compose.improve')}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        {t('compose.improveTip')}
        {s.split !== 'single' ? ` ${t('compose.improveOneClip')}` : ''}
      </TooltipContent>
    </Tooltip>
  )
}

function parseNumber(raw: string, integer: boolean): number | null {
  if (raw.trim() === '') return null
  const n = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw)
  return Number.isFinite(n) ? n : null
}

type Level = { key: Key; value: number }
const DETAIL_LEVELS: Level[] = [
  { key: 'compose.tune.level.quick', value: 20 },
  { key: 'compose.tune.level.normal', value: 30 },
  { key: 'compose.tune.level.extra', value: 40 },
]
const MOTION_LEVELS: Level[] = [
  { key: 'compose.tune.level.calm', value: 8 },
  { key: 'compose.tune.level.normal', value: 12 },
  { key: 'compose.tune.level.lively', value: 20 },
]
const SOUND_LEVELS: Level[] = [
  { key: 'compose.tune.level.normal', value: 3 },
  { key: 'compose.tune.level.more', value: 6 },
]

/** One setting as named levels, with a number of your own for anyone who wants it.
 *  `value` is what the store holds (null = the quality's own value, `fallback`). */
function LevelPicker({ id, label, help, levels, value, fallback, min, max, step, onChange }: {
  id: string
  label: string
  help: string
  levels: Level[]
  value: number | null
  fallback: number
  min: number
  max: number
  step: number
  onChange: (value: number | null) => void
}) {
  const t = useT()
  const effective = value ?? fallback
  const match = levels.find((l) => l.value === effective)
  const [custom, setCustom] = useState(() => value !== null && !match)
  const selected = custom || !match ? 'custom' : match.key
  return (
    <div className="grid gap-1">
      <span id={`${id}-label`} className="text-xs font-medium">{label}</span>
      <p className="text-2xs text-muted-foreground">{help}</p>
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        value={selected}
        aria-labelledby={`${id}-label`}
        className="max-w-full flex-wrap"
        onValueChange={(v) => {
          if (!v) return
          if (v === 'custom') {
            setCustom(true)
            if (value === null) onChange(fallback)
            return
          }
          setCustom(false)
          const level = levels.find((l) => l.key === v)
          if (level) onChange(level.value === fallback ? null : level.value)
        }}
      >
        {levels.map((l) => (
          <ToggleGroupItem key={l.key} value={l.key} className="px-2.5 text-xs">
            {t(l.key)} <span className="text-muted-foreground">{l.value}</span>
          </ToggleGroupItem>
        ))}
        <ToggleGroupItem value="custom" className="px-2.5 text-xs">{t('compose.tune.level.custom')}</ToggleGroupItem>
      </ToggleGroup>
      {selected === 'custom' ? (
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          aria-label={t('compose.tune.customValue', { label })}
          value={value ?? ''}
          onChange={(e) => onChange(parseNumber(e.target.value, step === 1))}
          className="h-8 w-28"
        />
      ) : null}
    </div>
  )
}

/** Detail, motion, sound variation and the seed, as plain choices. Collapsed by
 *  default: a clip that never opens this renders exactly as the quality says. */
function FineTuning({ preset }: { preset: Preset | undefined }) {
  const t = useT()
  const s = useCompose()
  const [open, setOpen] = useState(
    () => s.steps !== null || s.shiftVideo !== null || s.shiftAudio !== null || s.width !== null || s.seed !== null,
  )
  const error = controlsError(s)
  const quick = s.preset === 'turbo'
  return (
    <div className="col-span-2 rounded-md border">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-start text-sm font-medium"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>
          {t('compose.advanced')}
          {!open && error ? <span className="ms-2 text-xs text-destructive">{error}</span> : null}
        </span>
        <ChevronDown className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-180')} />
      </button>
      {open ? (
        <div className="grid gap-4 border-t px-3 py-3">
          <p className="text-2xs text-muted-foreground">
            {t('compose.tune.help')}{' '}
            <Link to="/beta#controls" className="underline underline-offset-2">
              {t('compose.tune.explained')}
            </Link>
          </p>
          {quick ? (
            <div className="grid gap-1">
              <span className="text-xs font-medium">{t('compose.tune.detail')}</span>
              <p className="text-2xs text-muted-foreground">{t('compose.tune.detailQuick')}</p>
              {s.steps !== null ? (
                <p className="text-2xs text-warn">
                  {t('compose.tune.detailQuickSet', { n: s.steps })}{' '}
                  <button type="button" className="underline underline-offset-2" onClick={() => s.setControls({ steps: null })}>
                    {t('compose.tune.useFast')}
                  </button>
                </p>
              ) : null}
            </div>
          ) : (
            <LevelPicker
              id="tune-detail"
              label={t('compose.tune.detail')}
              help={t('compose.tune.detailHelp')}
              levels={DETAIL_LEVELS}
              value={s.steps}
              fallback={preset?.steps ?? 30}
              min={STEPS.min}
              max={STEPS.max}
              step={1}
              onChange={(v) => s.setControls({ steps: v })}
            />
          )}
          <LevelPicker
            id="tune-motion"
            label={t('compose.tune.motion')}
            help={t('compose.tune.motionHelp')}
            levels={MOTION_LEVELS}
            value={s.shiftVideo}
            fallback={DEFAULT_SHIFT.video}
            min={SHIFT.min}
            max={SHIFT.max}
            step={0.5}
            onChange={(v) => s.setControls({ shiftVideo: v })}
          />
          <LevelPicker
            id="tune-sound"
            label={t('compose.tune.soundVar')}
            help={t('compose.tune.soundVarHelp')}
            levels={SOUND_LEVELS}
            value={s.shiftAudio}
            fallback={DEFAULT_SHIFT.audio}
            min={SHIFT.min}
            max={SHIFT.max}
            step={0.5}
            onChange={(v) => s.setControls({ shiftAudio: v })}
          />
          <div className="grid gap-1">
            <Label htmlFor="tune-seed" className="text-xs font-medium">
              {t('compose.tune.seed')}
            </Label>
            <p className="text-2xs text-muted-foreground">{t('compose.tune.seedHelp')}</p>
            <Input
              id="tune-seed"
              inputMode="numeric"
              placeholder={t('compose.tune.newRandom')}
              value={s.seed ?? ''}
              onChange={(e) => s.setControls({ seed: parseNumber(e.target.value, true) })}
              className="h-8 w-44"
            />
            {s.seed !== null && s.takes > 1 ? <p className="text-2xs text-muted-foreground">{t('compose.tune.seedTakes')}</p> : null}
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
      ) : null}
    </div>
  )
}

const TRANSITIONS: { value: Transition; label: Key }[] = [
  { value: 'cut', label: 'compose.cutTo' },
  { value: 'match', label: 'compose.matchCut' },
  { value: 'continuous', label: 'compose.continuous' },
]

/** Several shots inside one clip. The model reads "SHOT 1 … SHOT 2" and edits
 *  inside the render: one world, one colour, one soundtrack across the cuts. */
function ShotsEditor() {
  const t = useT()
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
            <span className="text-xs font-medium">{t('compose.shot', { n: i + 1 })}</span>
            {i > 0 ? (
              <Select value={shot.transition} onValueChange={(v) => update(shot.id, { transition: v as Transition })}>
                <SelectTrigger aria-label={t('compose.transition', { n: i + 1 })} className="h-7 w-auto min-w-28 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRANSITIONS.map((tr) => (
                    <SelectItem key={tr.value} value={tr.value}>
                      {t(tr.label)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <span className="text-2xs text-muted-foreground">{t('compose.opensOnRef')}</span>
            )}
            <div className="flex-1" />
            {shots.length > 1 ? (
              <Button size="icon" variant="ghost" className="size-7" aria-label={t('compose.removeShot', { n: i + 1 })} onClick={() => remove(shot.id)}>
                <Trash2 className="size-3.5" />
              </Button>
            ) : null}
          </div>
          <Textarea
            aria-label={t('compose.shot', { n: i + 1 })}
            value={shot.text}
            onChange={(e) => update(shot.id, { text: e.target.value })}
            placeholder={t(i === 0 ? 'compose.shotPh1' : 'compose.shotPh')}
            rows={2}
            className="min-h-16 resize-none text-sm leading-relaxed"
          />
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={shots.length >= MAX_SHOTS} onClick={() => s.setShots([...shots, newShot()])}>
          <Plus /> {t('compose.addShot')}
        </Button>
        <p className="text-2xs text-muted-foreground">
          {filled ? tn('compose.shotsCountOne', 'compose.shotsCountMany', filled, { s: s.seconds, each: perShot.toFixed(1) }) : t('compose.shotsMax')}
          {filled > 1 && perShot < MIN_SECONDS_PER_SHOT ? t('compose.rushed') : ''}
          {' '}
          <Link to="/beta#shots" className="underline underline-offset-2">
            {t('compose.howItWorks')}
          </Link>
        </p>
      </div>
    </div>
  )
}


/** Images pinned at a moment inside the clip. The start and end frames have
 *  their own slots; these anchor everything in between. */
function KeyframesSection() {
  const t = useT()
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
          <span className="text-sm font-medium">{t('compose.keyframes')}</span>
          <p className="text-2xs text-muted-foreground">
            {t('compose.keyframesHelp')}{' '}
            <Link to="/beta#keyframes" className="underline underline-offset-2">
              {t('compose.howItWorks')}
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
          <ImagePlus /> {t('compose.addKeyframe')}
        </Button>
      </div>
      {s.keyframes.length ? (
        <ul className="grid gap-2">
          {s.keyframes.map((k, i) => (
            <li key={k.tile.id} className="flex items-center gap-3">
              <ImageTile tile={k.tile} className="aspect-video w-24 shrink-0" />
              <Label htmlFor={`keyframe-at-${k.tile.id}`} className="text-xs text-muted-foreground">
                {t('compose.keyframeAt', { n: i + 1 })}
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
              <span className="text-xs text-muted-foreground">{t('compose.sOf', { n: s.seconds })}</span>
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
  const t = useT()
  const audio = useCompose((s) => s.audio)
  const { handleFiles, remove, retry, canRetry } = useReferenceUploads()
  const input = useRef<HTMLInputElement>(null)
  return (
    <div className="col-span-2 grid gap-2 rounded-md border px-3 py-2">
      <div className="flex items-center gap-2">
        <div className="grid gap-0.5">
          <span className="text-sm font-medium">{t('compose.audioTrack')}</span>
          <p className="text-2xs text-muted-foreground">
            {t('compose.audioTrackHelp')}{' '}
            <Link to="/beta#lipsync" className="underline underline-offset-2">
              {t('compose.howItWorks')}
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
            <AudioLines /> {t('compose.addAudio')}
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
                ? (audio.error ?? t('common.uploadFailed'))
                : audio.status === 'uploading'
                  ? t('compose.uploadingPct', { pct: Math.round(audio.progress * 100) })
                  : t('compose.audioReady')}
            </p>
          </div>
          {audio.status === 'error' && canRetry(audio.id) ? (
            <Button size="sm" variant="ghost" onClick={() => retry(audio.id)}>
              {t('common.retry')}
            </Button>
          ) : null}
          <Button size="icon" variant="ghost" className="size-8" aria-label={t('compose.removeAudio')} onClick={() => remove(audio.id)}>
            <X className="size-4" />
          </Button>
        </div>
      ) : null}
    </div>
  )
}


/** A reference of any kind, as a row: a video with its first frame, or an audio clip. */
function MediaRefRow({ tile, kind }: { tile: RefTile; kind: 'video' | 'audio' }) {
  const t = useT()
  const { remove, retry, canRetry } = useReferenceUploads()
  return (
    <li className="flex items-center gap-3 rounded-md bg-field p-2">
      {kind === 'video' ? (
        <div className="aspect-video w-20 shrink-0 overflow-hidden rounded bg-black">
          {tile.previewUrl ? (
            <video src={tile.previewUrl} preload="metadata" muted className="size-full object-cover" />
          ) : tile.key ? (
            <RefThumb objectKey={tile.key} className="size-full" />
          ) : null}
        </div>
      ) : (
        <AudioLines className="size-4 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">{tile.name}</p>
        <p className={cn('text-2xs', tile.status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
          {tile.status === 'error' ? (tile.error ?? t('common.uploadFailed')) : tile.status === 'uploading' ? t('compose.uploadingPct', { pct: Math.round(tile.progress * 100) }) : t('compose.ready')}
        </p>
      </div>
      {tile.status === 'error' && canRetry(tile.id) ? (
        <Button size="sm" variant="ghost" onClick={() => retry(tile.id)}>
          {t('common.retry')}
        </Button>
      ) : null}
      <Button size="icon" variant="ghost" className="size-8" aria-label={t('compose.removeTile', { name: tile.name })} onClick={() => remove(tile.id)}>
        <X className="size-4" />
      </Button>
    </li>
  )
}

/** References mode: images, whole short videos and audio clips, each addressed
 *  in the prompt by its tag in the order it sits here. */
function ReferencesBlock({ onPickImages }: { onPickImages: () => void }) {
  const t = useT()
  const s = useCompose()
  const { handleFiles } = useReferenceUploads()
  const videoInput = useRef<HTMLInputElement>(null)
  const audioInput = useRef<HTMLInputElement>(null)
  const tags = [
    ...s.refs.slice(0, MAX_REF_IMAGES).map((_, i) => `<Picture ${i + 1}>`),
    ...s.refVideos.map((_, i) => `<Video ${i + 1}>`),
    ...s.refAudios.map((_, i) => `<Audio ${i + 1}>`),
  ]
  return (
    <div className="grid gap-3">
      <div className="grid gap-2">
        <Label>{t('compose.refImages', { n: MAX_REF_IMAGES })}</Label>
        <RefTiles onPick={onPickImages} />
      </div>
      <div className="grid gap-2">
        <div className="flex items-center gap-2">
          <Label>{t('compose.refVideos', { n: MAX_REF_MEDIA })}</Label>
          <div className="flex-1" />
          <input ref={videoInput} type="file" hidden multiple accept="video/*" onChange={(e) => { if (e.target.files) handleFiles(e.target.files); e.target.value = '' }} />
          <Button size="sm" variant="outline" disabled={s.refVideos.length >= MAX_REF_MEDIA} onClick={() => videoInput.current?.click()}>
            <Film /> {t('compose.addVideo')}
          </Button>
        </div>
        {s.refVideos.length ? (
          <ul className="grid gap-2">
            {s.refVideos.map((tile) => (
              <MediaRefRow key={tile.id} tile={tile} kind="video" />
            ))}
          </ul>
        ) : null}
      </div>
      <div className="grid gap-2">
        <div className="flex items-center gap-2">
          <Label>{t('compose.refAudio', { n: MAX_REF_MEDIA })}</Label>
          <div className="flex-1" />
          <input ref={audioInput} type="file" hidden multiple accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac" onChange={(e) => { if (e.target.files) handleFiles(e.target.files); e.target.value = '' }} />
          <Button size="sm" variant="outline" disabled={s.refAudios.length >= MAX_REF_MEDIA} onClick={() => audioInput.current?.click()}>
            <AudioLines /> {t('compose.addRefAudio')}
          </Button>
        </div>
        {s.refAudios.length ? (
          <ul className="grid gap-2">
            {s.refAudios.map((tile) => (
              <MediaRefRow key={tile.id} tile={tile} kind="audio" />
            ))}
          </ul>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground">
        {tags.length ? (
          <>
            {t('compose.callThem')}{tags.map((tag, i) => (
              <span key={tag}>
                {i ? ', ' : ''}
                <code className="rounded bg-field px-1">{tag}</code>
              </span>
            ))}{t('compose.driveThem')}
          </>
        ) : (
          t('compose.addOneRef')
        )}{' '}
        <Link to="/beta#ref2v" className="underline underline-offset-2">
          {t('compose.howItWorks')}
        </Link>
      </p>
    </div>
  )
}
