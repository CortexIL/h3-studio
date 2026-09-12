import { ImagePlus, Loader2, RotateCw, Sparkles, X } from 'lucide-react'
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
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MODE_LABEL, fmtDuration, fmtUsd, presetLabel, presetSize } from '@/lib/format'
import { useDebouncedValue } from '@/lib/hooks'
import { imageUrl } from '@/lib/media'
import { COMPOSE_MODES } from '@/lib/modes'
import { cn } from '@/lib/utils'

import { SECONDS, TAKES, clipCount, draftOf, useCompose } from './composeStore'
import { useFilePaste, useReferenceUploads } from './useReferenceUploads'

function RefTiles({ onPick }: { onPick: () => void }) {
  const refs = useCompose((s) => s.refs)
  const { retry, remove, canRetry } = useReferenceUploads()
  return (
    <div className="flex flex-wrap gap-2">
      {refs.map((r) => (
        <div key={r.id} className="group relative size-16 overflow-hidden rounded-md border bg-field" title={r.name}>
          <img
            src={r.previewUrl ?? (r.key ? imageUrl(r.key) : undefined)}
            alt={r.name}
            className={cn('size-full object-cover', r.status !== 'ready' && 'opacity-50')}
          />
          {r.status === 'uploading' ? (
            <div className="absolute inset-x-1 bottom-1 h-1 overflow-hidden rounded-full bg-black/60" aria-label={`Uploading ${r.name}`}>
              <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round(r.progress * 100)}%` }} />
            </div>
          ) : null}
          {r.status === 'error' ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => canRetry(r.id) && retry(r.id)}
                  className="absolute inset-0 grid place-items-center bg-destructive/25 text-destructive"
                  aria-label={`Retry uploading ${r.name}`}
                >
                  <RotateCw className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent>{r.error ?? 'Upload failed'} · click to retry</TooltipContent>
            </Tooltip>
          ) : null}
          <button
            type="button"
            onClick={() => remove(r.id)}
            aria-label={`Remove ${r.name}`}
            className="absolute top-1 right-1 grid size-5 place-items-center rounded-full bg-black/70 text-white opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
          >
            <X className="size-3" />
          </button>
        </div>
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

  const readyKeys = s.refs.filter((r) => r.status === 'ready' && r.key).map((r) => r.key as string)
  const uploading = s.refs.some((r) => r.status === 'uploading')
  const count = clipCount(s.prompt, s.split, s.takes)

  const body = useMemo<NewJobsBody>(
    () => ({ prompts: s.prompt, split: s.split, seconds: s.seconds, preset: s.preset, mode: s.mode, count: s.takes, ref_images: readyKeys }),
    // readyKeys is derived from refs; depend on the joined keys, not a new array each render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [s.prompt, s.split, s.seconds, s.preset, s.mode, s.takes, readyKeys.join('|')],
  )

  const submit = () => {
    if (!count || uploading || add.isPending) return
    add.mutate(body, { onSuccess: () => useCompose.getState().clearDraft() })
  }

  const clear = () => {
    const snapshot = draftOf(useCompose.getState())
    if (!snapshot.prompt && !snapshot.refs.length) return
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
        </div>

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
          <div className="grid gap-2">
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
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

      </div>

      <footer className="shrink-0 border-t p-4">
        {/* The cost sits beside the button that spends it, never scrolled out of view. */}
        <div className="mb-3 empty:hidden">
          <EstimateLine body={body} />
        </div>
        <Button className="h-10 w-full gap-2 text-sm" disabled={!count || uploading || add.isPending} onClick={submit}>
          {add.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          {uploading
            ? 'Waiting for uploads…'
            : count > 1
              ? `Add ${count} clips to the queue`
              : 'Add to the queue'}
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
