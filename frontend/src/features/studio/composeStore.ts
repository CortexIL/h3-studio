import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { Job, Mode, NewJobsBody, PublicConfig } from '@/api/types'

export type Split = 'single' | 'lines' | 'shots'

/** How a shot joins the one before it. H3 reads these words inside one prompt and
 *  edits inside the clip: one world, matching colour, one soundtrack. */
export type Transition = 'cut' | 'match' | 'continuous'

export interface Shot {
  id: string
  text: string
  transition: Transition
}

export const MAX_SHOTS = 6
/** Under this a shot is a flash; the editor warns rather than forbids. */
export const MIN_SECONDS_PER_SHOT = 2.5

const TRANSITION_WORDS: Record<Transition, string> = {
  cut: 'Cut to',
  match: 'Match cut to',
  continuous: 'Without a cut, the camera moves on to',
}

export function newShot(): Shot {
  return { id: crypto.randomUUID(), text: '', transition: 'cut' }
}

/** The single prompt the model sees for a multi-shot clip: SHOT 1, SHOT 2 … */
export function assembleShots(shots: Shot[]): string {
  const filled = shots.filter((sh) => sh.text.trim())
  return filled
    .map((sh, i) => {
      const text = sh.text.trim()
      const opener = i === 0 ? text : `${TRANSITION_WORDS[sh.transition]} ${text.charAt(0).toLowerCase()}${text.slice(1)}`
      return `SHOT ${i + 1}: ${opener}`
    })
    .join('\n')
}

/** The prompt text a draft stands for, whichever way it was written. */
export function promptText(s: Pick<Draft, 'prompt' | 'split' | 'shots'>): string {
  return s.split === 'shots' ? assembleShots(s.shots) : s.prompt
}

/** Where a picked image goes. Each mode reads its own slots and ignores the rest,
 *  which is what makes switching modes lossless without any switch-time logic. */
export type TileSlot = 'refs' | 'start' | 'end'

export interface RefTile {
  id: string
  name: string
  status: 'uploading' | 'ready' | 'error'
  progress: number
  /** Storage key once uploaded; this is what a job references. */
  key?: string
  /** A local blob: preview while uploading. Not persisted - it dies with the page. */
  previewUrl?: string
  error?: string
}

/** The clip an extension continues.
 *
 * Both ways in end at the same place - a key under the caller's own uploads -
 * because the server cuts the tail either way. Only the label and the thumbnail
 * differ, so the slot looks the same however the video got there.
 */
export interface ExtendSource {
  from: 'clip' | 'upload'
  label: string
  tile: RefTile
  posterUrl?: string
}

export interface Draft {
  prompt: string
  split: Split
  /** The shots of a multi-shot clip; only read when split is 'shots'. */
  shots: Shot[]
  seconds: number
  preset: string
  mode: Mode
  takes: number
  /** Whether the clip keeps the sound H3 generates. */
  keepAudio: boolean
  /** Sound direction: what it should sound like, and the music. */
  sound: string
  music: string
  /** Render controls on top of the preset. null = the preset's own value. */
  steps: number | null
  shiftVideo: number | null
  shiftAudio: number | null
  width: number | null
  height: number | null
  /** null = a fresh random seed. Only pinned for a single take. */
  seed: number | null
  refs: RefTile[]
  startFrame: RefTile | null
  endFrame: RefTile | null
  extendSource: ExtendSource | null
}

interface ComposeState extends Draft {
  initialized: boolean
  setPrompt: (value: string) => void
  setSplit: (value: Split) => void
  setShots: (shots: Shot[]) => void
  setSeconds: (value: number) => void
  setPreset: (value: string) => void
  setMode: (value: Mode) => void
  setTakes: (value: number) => void
  setKeepAudio: (value: boolean) => void
  setSound: (value: string) => void
  setMusic: (value: string) => void
  setControls: (patch: Partial<Controls>) => void
  addTile: (slot: TileSlot, tile: RefTile) => void
  updateTile: (id: string, patch: Partial<RefTile>) => void
  removeTile: (id: string) => void
  setExtendSource: (source: ExtendSource | null) => void
  applyDefaults: (config: PublicConfig) => void
  loadFromJob: (
    job: Pick<Job, 'prompt' | 'seconds' | 'preset' | 'mode' | 'ref_images'>
      & Partial<Pick<Job, 'keep_audio' | 'sound' | 'music' | 'steps' | 'shift_video' | 'shift_audio' | 'width' | 'height'>>,
  ) => void
  clearDraft: () => void
  restore: (draft: Draft) => void
}

export const SECONDS = { min: 4, max: 15 } as const
export const STEPS = { min: 1, max: 60 } as const
export const SHIFT = { min: 0.5, max: 40 } as const
export const SIZE = { min: 256, max: 2048, multiple: 32, maxArea: 1920 * 1088 } as const
export const DEFAULT_SHIFT = { video: 12, audio: 3 } as const

export type Controls = Pick<Draft, 'steps' | 'shiftVideo' | 'shiftAudio' | 'width' | 'height' | 'seed'>
export const TAKES = { min: 1, max: 10 } as const

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, Math.round(Number.isFinite(value) ? value : min)))

const patched = (tile: RefTile | null, id: string, patch: Partial<RefTile>) =>
  tile && tile.id === id ? { ...tile, ...patch } : tile

/**
 * The compose form. It lives outside React Query on purpose: polls re-read
 * server state every few seconds, and the form must never be overwritten by
 * one. It persists to sessionStorage, so a draft survives a trip to the
 * Archive and back.
 */
export const useCompose = create<ComposeState>()(
  persist(
    (set) => ({
      prompt: '',
      split: 'single',
      shots: [],
      seconds: 10,
      preset: 'final',
      mode: 'i2v',
      takes: 1,
      keepAudio: true,
      sound: '',
      music: '',
      steps: null,
      shiftVideo: null,
      shiftAudio: null,
      width: null,
      height: null,
      seed: null,
      refs: [],
      startFrame: null,
      endFrame: null,
      extendSource: null,
      initialized: false,
      setPrompt: (prompt) => set({ prompt }),
      // Entering shots mode with nothing there starts two empty shots: the form
      // says what it is before a word is typed.
      setSplit: (split) =>
        set((s) => ({ split, shots: split === 'shots' && s.shots.length < 2 ? [newShot(), newShot()] : s.shots })),
      setShots: (shots) => set({ shots }),
      setSeconds: (seconds) => set({ seconds: clamp(seconds, SECONDS.min, SECONDS.max) }),
      setPreset: (preset) => set({ preset }),
      setMode: (mode) => set({ mode }),
      setTakes: (takes) => set({ takes: clamp(takes, TAKES.min, TAKES.max) }),
      setKeepAudio: (keepAudio) => set({ keepAudio }),
      setSound: (sound) => set({ sound }),
      setMusic: (music) => set({ music }),
      setControls: (patch) => set(patch),
      // 'refs' collects; a frame slot holds exactly one, so it replaces.
      addTile: (slot, tile) =>
        set((s) =>
          slot === 'refs'
            ? { refs: [...s.refs, tile] }
            : slot === 'start'
              ? { startFrame: tile }
              : { endFrame: tile },
        ),
      // By id, not by slot: an upload's progress callback only ever knows the id,
      // so it keeps working wherever the tile happens to live.
      updateTile: (id, patch) =>
        set((s) => ({
          refs: s.refs.map((r) => (r.id === id ? { ...r, ...patch } : r)),
          startFrame: patched(s.startFrame, id, patch),
          endFrame: patched(s.endFrame, id, patch),
          extendSource:
            s.extendSource && s.extendSource.tile.id === id
              ? { ...s.extendSource, tile: { ...s.extendSource.tile, ...patch } }
              : s.extendSource,
        })),
      removeTile: (id) =>
        set((s) => ({
          refs: s.refs.filter((r) => r.id !== id),
          startFrame: s.startFrame?.id === id ? null : s.startFrame,
          endFrame: s.endFrame?.id === id ? null : s.endFrame,
          extendSource: s.extendSource?.tile.id === id ? null : s.extendSource,
        })),
      setExtendSource: (extendSource) => set({ extendSource }),
      // Once per session: the server's defaults seed the form, then the
      // user's own choices win.
      applyDefaults: (config) =>
        set((s) =>
          s.initialized
            ? s
            : {
                initialized: true,
                seconds: clamp(config.default_seconds, SECONDS.min, SECONDS.max),
                preset: config.presets[config.default_preset] ? config.default_preset : s.preset,
                mode: config.default_mode,
                keepAudio: config.keep_audio ?? s.keepAudio,
              },
        ),
      // "Use again": the job's prompt is one prompt, even if it has line
      // breaks - restoring it in "Line = clip" mode would silently split it.
      loadFromJob: (job) => {
        const tiles = job.ref_images.map((key, i) => ({
          id: `job-ref-${i}-${key}`,
          name: key.split('/').pop() ?? 'reference',
          status: 'ready' as const,
          progress: 1,
          key,
        }))
        const frames = job.mode === 'flf2v'
        const extending = job.mode === 'extend'
        set((s) => ({
          prompt: job.prompt,
          split: 'single',
          seconds: clamp(job.seconds, SECONDS.min, SECONDS.max),
          preset: job.preset,
          mode: job.mode,
          takes: 1,
          // Every slot is set, never merged: a leftover frame from the previous
          // draft would ride along into a job that has nothing to do with it.
          refs: frames || extending ? [] : tiles,
          startFrame: frames ? tiles[0] ?? null : null,
          endFrame: frames ? tiles[1] ?? null : null,
          extendSource:
            extending && tiles[0]
              ? { from: 'upload', label: tiles[0].name, tile: tiles[0] }
              : null,
          // A clip that made no choice followed the server's setting, which is
          // what the switch already shows - so leave it where it is.
          keepAudio: job.keep_audio ?? s.keepAudio,
          sound: job.sound ?? '',
          music: job.music ?? '',
          steps: job.steps ?? null,
          shiftVideo: job.shift_video ?? null,
          shiftAudio: job.shift_audio ?? null,
          width: job.width ?? null,
          height: job.height ?? null,
          // Never the seed: reusing it would reproduce the same clip.
          seed: null,
        }))
      },
      clearDraft: () =>
        set((s) => ({
          prompt: '', sound: '', music: '', refs: [], startFrame: null, endFrame: null, extendSource: null,
          shots: s.split === 'shots' ? [newShot(), newShot()] : s.shots,
        })),
      restore: (draft) => set({ ...draft }),
    }),
    {
      name: 'h3.compose',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (s) => ({
        prompt: s.prompt,
        split: s.split,
        shots: s.shots,
        seconds: s.seconds,
        preset: s.preset,
        mode: s.mode,
        takes: s.takes,
        keepAudio: s.keepAudio,
        sound: s.sound,
        music: s.music,
        steps: s.steps,
        shiftVideo: s.shiftVideo,
        shiftAudio: s.shiftAudio,
        width: s.width,
        height: s.height,
        seed: s.seed,
        initialized: s.initialized,
        refs: s.refs.filter(persistable).map(stripPreview),
        startFrame: s.startFrame && persistable(s.startFrame) ? stripPreview(s.startFrame) : null,
        endFrame: s.endFrame && persistable(s.endFrame) ? stripPreview(s.endFrame) : null,
        extendSource:
          s.extendSource && persistable(s.extendSource.tile)
            ? { ...s.extendSource, tile: stripPreview(s.extendSource.tile) }
            : null,
      }),
    },
  ),
)

// Only a tile the server already holds is worth keeping: a blob: URL is dead
// after a reload, and an upload in flight did not survive the navigation either.
function persistable(tile: RefTile): boolean {
  return tile.status === 'ready' && Boolean(tile.key)
}

function stripPreview({ previewUrl: _preview, ...rest }: RefTile): Omit<RefTile, 'previewUrl'> {
  return rest
}

export function draftOf(state: Draft): Draft {
  const { prompt, split, shots, seconds, preset, mode, takes, keepAudio, sound, music, steps,
    shiftVideo, shiftAudio, width, height, seed, refs, startFrame, endFrame, extendSource } = state
  return { prompt, split, shots, seconds, preset, mode, takes, keepAudio, sound, music, steps,
    shiftVideo, shiftAudio, width, height, seed, refs, startFrame, endFrame, extendSource }
}

export function clipCount(prompt: string, split: Split, takes: number): number {
  // In shots mode `prompt` is the assembled text: one clip, or none while empty.
  const prompts = split === 'lines' ? prompt.split('\n').filter((l) => l.trim()).length : prompt.trim() ? 1 : 0
  return prompts * takes
}

/** The tiles the current mode actually sends. Everything else is held, not used. */
export function tilesUsedBy(s: Draft): RefTile[] {
  if (s.mode === 'flf2v') return [s.startFrame, s.endFrame].filter(Boolean) as RefTile[]
  // The end frame is optional here: the source says where the clip comes from,
  // and a last frame - if given - says where it arrives.
  if (s.mode === 'extend') {
    return [s.extendSource?.tile, s.endFrame].filter(Boolean) as RefTile[]
  }
  if (s.mode === 't2v') return []
  return s.refs
}

/** Why the queue button is disabled, or null when it is not. */
export type Block =
  | 'prompt' | 'uploading' | 'upload-failed' | 'start-frame' | 'end-frame' | 'extend-source' | 'controls'

/** Why the render controls cannot be sent as they are, or null. Mirrors the server. */
export function controlsError(s: Controls): string | null {
  if (s.steps !== null && (s.steps < STEPS.min || s.steps > STEPS.max)) return `Steps: ${STEPS.min}–${STEPS.max}`
  for (const v of [s.shiftVideo, s.shiftAudio]) {
    if (v !== null && (v < SHIFT.min || v > SHIFT.max)) return `Motion: ${SHIFT.min}–${SHIFT.max}`
  }
  if ((s.width === null) !== (s.height === null)) return 'Width and height go together'
  if (s.width !== null && s.height !== null) {
    for (const v of [s.width, s.height]) {
      if (v < SIZE.min || v > SIZE.max) return `Size: ${SIZE.min}–${SIZE.max} px`
      if (v % SIZE.multiple) return `Size: multiples of ${SIZE.multiple}`
    }
    if (s.width * s.height > SIZE.maxArea) return 'Size: at most 1920×1088'
  }
  return null
}

export function blockedBy(s: Draft): Block | null {
  if (!clipCount(promptText(s), s.split, s.takes)) return 'prompt'
  const used = tilesUsedBy(s)
  // Scoped to the mode's own slots: a stuck upload held aside for another mode
  // must not block this one.
  if (used.some((t) => t.status === 'uploading')) return 'uploading'
  if (used.some((t) => t.status === 'error')) return 'upload-failed'
  if (s.mode === 'flf2v') {
    if (!s.startFrame?.key) return 'start-frame'
    if (!s.endFrame?.key) return 'end-frame'
  }
  if (s.mode === 'extend' && !s.extendSource?.tile.key) return 'extend-source'
  if (controlsError(s)) return 'controls'
  return null
}

/** The overrides a draft sends: only what was set, so the server keeps preset defaults. */
export function controlsPayload(s: Controls, takes: number): Partial<NewJobsBody> {
  return {
    ...(s.steps !== null ? { steps: s.steps } : {}),
    ...(s.shiftVideo !== null ? { shift_video: s.shiftVideo } : {}),
    ...(s.shiftAudio !== null ? { shift_audio: s.shiftAudio } : {}),
    ...(s.width !== null && s.height !== null ? { width: s.width, height: s.height } : {}),
    // Several takes sharing a seed would come out identical.
    ...(s.seed !== null && takes === 1 ? { seed: s.seed } : {}),
  }
}

const readyKeys = (tiles: RefTile[]) =>
  tiles.filter((t) => t.status === 'ready' && t.key).map((t) => t.key as string)

/** The one place a draft becomes a request, so nothing extraneous can be sent. */
export function toPayload(s: Draft): NewJobsBody {
  const base = {
    // Shots become one prompt; the server never splits it.
    prompts: promptText(s),
    split: s.split === 'shots' ? ('single' as const) : s.split,
    seconds: s.seconds,
    preset: s.preset,
    mode: s.mode,
    count: s.takes,
    keep_audio: s.keepAudio,
    ...controlsPayload(s, s.takes),
    // Direction travels only with sound on and only when written: an empty
    // field must not become an empty "Audio:" line in the prompt.
    ...(s.keepAudio && s.sound.trim() ? { sound: s.sound.trim() } : {}),
    ...(s.keepAudio && s.music.trim() ? { music: s.music.trim() } : {}),
  }
  if (s.mode === 't2v') return { ...base, ref_images: [] }
  if (s.mode === 'flf2v' || s.mode === 'extend') {
    return { ...base, ref_images: readyKeys(tilesUsedBy(s)) }
  }
  return { ...base, ref_images: readyKeys(s.refs) }
}
