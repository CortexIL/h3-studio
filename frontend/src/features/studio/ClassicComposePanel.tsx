import { ArrowRight, Loader2, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { toast } from 'sonner'

import { useAddJobs } from '@/api/mutations'
import { useStatus } from '@/api/queries'
import type { Mode, NewJobsBody } from '@/api/types'
import { NumberStepper } from '@/components/app/NumberStepper'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { tn, useT, type Key } from '@/i18n'
import { presetSize } from '@/lib/format'
import { cn } from '@/lib/utils'

import { EstimateLine, ExtendSlot, FrameSlot, RefTiles } from './ComposePanel'
import { BLOCK_KEY, CLASSIC_MODES, NOT_CLASSIC_PRESETS, classicBlockedBy, classicPayload } from './classic'
import { SECONDS, TAKES, clipCount, draftOf, tilesUsedBy, useCompose } from './composeStore'
import { useFilePaste, useReferenceUploads } from './useReferenceUploads'

/** The Create panel as it was before the beta, on today's store and API.
 *
 * For anyone who preferred the studio before the beta: the same four modes, the
 * old names (Mode, Prompt, Seconds, Takes, Quality, Turbo, Final), the Sound
 * switch, and nothing the beta added. A clip made here renders exactly as it
 * did then, because the request carries only what the panel shows.
 */

export function ClassicComposePanel() {
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

  // A draft left by the new panel may hold what classic cannot show. Bring it
  // back to something this panel can read, once, without touching the prompt.
  useEffect(() => {
    const st = useCompose.getState()
    if (!CLASSIC_MODES.includes(st.mode)) st.setMode('i2v')
    if (st.split === 'shots') st.setSplit('single')
  }, [])
  useEffect(() => {
    if (config && NOT_CLASSIC_PRESETS.has(useCompose.getState().preset)) {
      useCompose.getState().setPreset(config.default_preset)
    }
  }, [config])

  const split = s.split === 'shots' ? 'single' : s.split
  const count = clipCount(s.prompt, split, s.takes)
  const block = classicBlockedBy(s)
  const usedKeys = tilesUsedBy(s).map((tile) => tile.key ?? tile.id).join('|')

  const payload = useMemo(
    () => classicPayload(s),
    // usedKeys stands in for the tiles, which are new objects on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [s.prompt, s.split, s.seconds, s.preset, s.mode, s.takes, s.keepAudio, usedKeys],
  )

  const estimateBody = useMemo<NewJobsBody>(
    () => ({ prompts: s.prompt, split, seconds: s.seconds, preset: s.preset, mode: s.mode, count: s.takes }),
    [s.prompt, split, s.seconds, s.preset, s.mode, s.takes],
  )

  const submit = () => {
    if (classicBlockedBy(useCompose.getState()) || add.isPending) return
    add.mutate(payload, { onSuccess: () => useCompose.getState().clearDraft() })
  }

  const clear = () => {
    const snapshot = draftOf(useCompose.getState())
    if (!snapshot.prompt && !snapshot.refs.length && !snapshot.startFrame && !snapshot.endFrame) return
    useCompose.getState().clearDraft()
    toast(t('classic.cleared'), { action: { label: t('common.undo'), onClick: () => useCompose.getState().restore(snapshot) } })
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragDepth(0)
    if (event.dataTransfer.files.length) handleFiles(event.dataTransfer.files)
  }
  const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')

  const presets = Object.entries(config?.presets ?? {}).filter(
    ([key, p]) => !p.hidden && !NOT_CLASSIC_PRESETS.has(key),
  )
  if (!presets.some(([key]) => key === s.preset)) presets.push([s.preset, config?.presets[s.preset] ?? null] as never)

  const lines = clipCount(s.prompt, 'lines', 1)

  return (
    <section
      aria-label={t('compose.title')}
      data-layout="classic"
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

      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4">
        <div className="grid gap-2">
          <Label htmlFor="classic-mode">{t('classic.mode')}</Label>
          <Select value={s.mode} onValueChange={(v) => s.setMode(v as Mode)}>
            <SelectTrigger id="classic-mode" className="h-9 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CLASSIC_MODES.map((m) => (
                <SelectItem key={m} value={m}>
                  {t(`classic.mode.${m}` as Key)}
                  <span className="text-muted-foreground"> · {t(`classic.hint.${m}` as Key)}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {s.mode === 'flf2v' ? (
          <div className="grid gap-2">
            <Label>{t('classic.frames')}</Label>
            <div className="flex items-start gap-2">
              <FrameSlot slot="start" label={t('classic.startFrame')} />
              <ArrowRight className="mt-7 size-4 shrink-0 text-muted-foreground rtl:rotate-180" />
              <FrameSlot slot="end" label={t('classic.endFrame')} />
            </div>
            <p className="text-xs text-muted-foreground">{t('classic.framesHelp')}</p>
            {s.refs.length ? (
              <p className="text-xs text-faint">{tn('classic.refsKeptOne', 'classic.refsKeptMany', s.refs.length)}</p>
            ) : null}
          </div>
        ) : s.mode === 'extend' ? (
          <ExtendSlot />
        ) : (
          <div className="grid gap-2">
            <Label>{t('classic.refs')}</Label>
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
            {s.mode === 't2v' && s.refs.length ? <p className="text-xs text-muted-foreground">{t('classic.t2vRefs')}</p> : null}
          </div>
        )}

        <div className="flex flex-[1_0_auto] flex-col gap-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="classic-prompt">{t('classic.prompt')}</Label>
            <div className="flex-1" />
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={split}
              onValueChange={(v) => v && s.setSplit(v as 'single' | 'lines')}
              aria-label={t('compose.splitAria')}
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="single" className="px-2.5 text-xs">
                    {t('classic.onePrompt')}
                  </ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>{t('classic.onePromptTip')}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <ToggleGroupItem value="lines" className="px-2.5 text-xs">
                    {t('classic.linePerClip')}
                  </ToggleGroupItem>
                </TooltipTrigger>
                <TooltipContent>{t('classic.linePerClipTip')}</TooltipContent>
              </Tooltip>
            </ToggleGroup>
          </div>
          <Textarea
            id="classic-prompt"
            value={s.prompt}
            onChange={(e) => s.setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                submit()
              }
            }}
            placeholder={split === 'lines' ? t('classic.promptPhLines') : t('classic.promptPh')}
            className="min-h-40 flex-1 resize-none text-base leading-relaxed"
          />
          {split === 'lines' && s.prompt.trim() ? (
            <p className="text-xs text-muted-foreground">
              {t('classic.linesToClips', {
                lines: tn('classic.lineOne', 'classic.lineMany', lines),
                clips: tn('classic.clipOne', 'classic.clipMany', count),
              })}
            </p>
          ) : null}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-2">
            <Label>{t('classic.seconds')}</Label>
            <NumberStepper label={t('classic.seconds')} value={s.seconds} min={SECONDS.min} max={SECONDS.max} onChange={s.setSeconds} />
          </div>
          <div className="grid gap-2">
            <Label>{t('classic.takes')}</Label>
            <NumberStepper label={t('classic.takes')} value={s.takes} min={TAKES.min} max={TAKES.max} onChange={s.setTakes} />
          </div>
          <div className="col-span-2 grid gap-2">
            <Label htmlFor="classic-preset">{t('classic.quality')}</Label>
            <Select value={s.preset} onValueChange={s.setPreset}>
              <SelectTrigger id="classic-preset" className="h-9 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {presets.map(([key, p]) => (
                  <SelectItem key={key} value={key}>
                    {t(`classic.preset.${key}` as Key)}
                    {p ? <span className="text-muted-foreground"> · {presetSize(p)}</span> : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="col-span-2 flex items-center justify-between gap-4 rounded-md border px-3 py-2">
            <div className="grid gap-0.5">
              <Label htmlFor="classic-audio">{t('classic.sound')}</Label>
              <p className="text-2xs text-muted-foreground">
                {s.keepAudio && s.preset === 'turbo' ? t('classic.soundTurbo') : t('classic.soundHelp')}
              </p>
            </div>
            <Switch id="classic-audio" checked={s.keepAudio} onCheckedChange={s.setKeepAudio} aria-label={t('classic.sound')} />
          </div>
        </div>
      </div>

      <footer className="shrink-0 border-t p-4">
        <div className="mb-3 empty:hidden">
          <EstimateLine body={estimateBody} />
        </div>
        <Button className="h-10 w-full gap-2 text-sm" disabled={block !== null || add.isPending} onClick={submit}>
          {add.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          {(block && BLOCK_KEY[block] && t(BLOCK_KEY[block])) || (count > 1 ? t('classic.addN', { n: count }) : t('classic.add'))}
        </Button>
        <p className="mt-2 text-center text-2xs text-faint">
          <kbd className="font-sans">⌘</kbd>/<kbd className="font-sans">Ctrl</kbd> + <kbd className="font-sans">Enter</kbd> {t('classic.kbd')}
        </p>
      </footer>

      {dragDepth > 0 ? (
        <div className={cn('pointer-events-none absolute inset-0 grid place-items-center rounded-xl border-2 border-dashed border-primary bg-background/80')}>
          <span className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">{t('classic.dropHint')}</span>
        </div>
      ) : null}
    </section>
  )
}
