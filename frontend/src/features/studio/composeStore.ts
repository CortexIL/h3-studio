import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { Job, Mode, NewJobsBody, PublicConfig } from '@/api/types'

export type Split = 'single' | 'lines'

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
  seconds: number
  preset: string
  mode: Mode
  takes: number
  /** Whether the clip keeps the sound H3 generates. */
  keepAudio: boolean
  /** Sound direction: what it should sound like, and the music. */
  sound: string
  music: string
  refs: RefTile[]
  startFrame: RefTile | null
  endFrame: RefTile | null
  extendSource: ExtendSource | null
}

interface ComposeState extends Draft {
  initialized: boolean
  setPrompt: (value: string) => void
  setSplit: (value: Split) => void
  setSeconds: (value: number) => void
  setPreset: (value: string) => void
  setMode: (value: Mode) => void
  setTakes: (value: number) => void
  setKeepAudio: (value: boolean) => void
  setSound: (value: string) => void
  setMusic: (value: string) => void
  addTile: (slot: TileSlot, tile: RefTile) => void
  updateTile: (id: string, patch: Partial<RefTile>) => void
  removeTile: (id: string) => void
  setExtendSource: (source: ExtendSource | null) => void
  applyDefaults: (config: PublicConfig) => void
  loadFromJob: (
    job: Pick<Job, 'prompt' | 'seconds' | 'preset' | 'mode' | 'ref_images'>
      & { keep_audio?: boolean | null; sound?: string | null; music?: string | null },
  ) => void
  clearDraft: () => void
  restore: (draft: Draft) => void
}

export const SECONDS = { min: 4, max: 15 } as const
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
      seconds: 10,
      preset: 'final',
      mode: 'i2v',
      takes: 1,
      keepAudio: true,
      sound: '',
      music: '',
      refs: [],
      startFrame: null,
      endFrame: null,
      extendSource: null,
      initialized: false,
      setPrompt: (prompt) => set({ prompt }),
      setSplit: (split) => set({ split }),
      setSeconds: (seconds) => set({ seconds: clamp(seconds, SECONDS.min, SECONDS.max) }),
      setPreset: (preset) => set({ preset }),
      setMode: (mode) => set({ mode }),
      setTakes: (takes) => set({ takes: clamp(takes, TAKES.min, TAKES.max) }),
      setKeepAudio: (keepAudio) => set({ keepAudio }),
      setSound: (sound) => set({ sound }),
      setMusic: (music) => set({ music }),
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
        }))
      },
      clearDraft: () =>
        set({ prompt: '', sound: '', music: '', refs: [], startFrame: null, endFrame: null, extendSource: null }),
      restore: (draft) => set({ ...draft }),
    }),
    {
      name: 'h3.compose',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (s) => ({
        prompt: s.prompt,
        split: s.split,
        seconds: s.seconds,
        preset: s.preset,
        mode: s.mode,
        takes: s.takes,
        keepAudio: s.keepAudio,
        sound: s.sound,
        music: s.music,
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
  const { prompt, split, seconds, preset, mode, takes, keepAudio, sound, music, refs,
    startFrame, endFrame, extendSource } = state
  return { prompt, split, seconds, preset, mode, takes, keepAudio, sound, music, refs,
    startFrame, endFrame, extendSource }
}

export function clipCount(prompt: string, split: Split, takes: number): number {
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
  | 'prompt' | 'uploading' | 'upload-failed' | 'start-frame' | 'end-frame' | 'extend-source'

export function blockedBy(s: Draft): Block | null {
  if (!clipCount(s.prompt, s.split, s.takes)) return 'prompt'
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
  return null
}

const readyKeys = (tiles: RefTile[]) =>
  tiles.filter((t) => t.status === 'ready' && t.key).map((t) => t.key as string)

/** The one place a draft becomes a request, so nothing extraneous can be sent. */
export function toPayload(s: Draft): NewJobsBody {
  const base = {
    prompts: s.prompt,
    split: s.split,
    seconds: s.seconds,
    preset: s.preset,
    mode: s.mode,
    count: s.takes,
    keep_audio: s.keepAudio,
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
