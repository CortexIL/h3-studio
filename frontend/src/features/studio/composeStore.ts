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

export interface Draft {
  prompt: string
  split: Split
  seconds: number
  preset: string
  mode: Mode
  takes: number
  refs: RefTile[]
  startFrame: RefTile | null
  endFrame: RefTile | null
}

interface ComposeState extends Draft {
  initialized: boolean
  setPrompt: (value: string) => void
  setSplit: (value: Split) => void
  setSeconds: (value: number) => void
  setPreset: (value: string) => void
  setMode: (value: Mode) => void
  setTakes: (value: number) => void
  addTile: (slot: TileSlot, tile: RefTile) => void
  updateTile: (id: string, patch: Partial<RefTile>) => void
  removeTile: (id: string) => void
  applyDefaults: (config: PublicConfig) => void
  loadFromJob: (job: Pick<Job, 'prompt' | 'seconds' | 'preset' | 'mode' | 'ref_images'>) => void
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
      refs: [],
      startFrame: null,
      endFrame: null,
      initialized: false,
      setPrompt: (prompt) => set({ prompt }),
      setSplit: (split) => set({ split }),
      setSeconds: (seconds) => set({ seconds: clamp(seconds, SECONDS.min, SECONDS.max) }),
      setPreset: (preset) => set({ preset }),
      setMode: (mode) => set({ mode }),
      setTakes: (takes) => set({ takes: clamp(takes, TAKES.min, TAKES.max) }),
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
        })),
      removeTile: (id) =>
        set((s) => ({
          refs: s.refs.filter((r) => r.id !== id),
          startFrame: s.startFrame?.id === id ? null : s.startFrame,
          endFrame: s.endFrame?.id === id ? null : s.endFrame,
        })),
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
        set({
          prompt: job.prompt,
          split: 'single',
          seconds: clamp(job.seconds, SECONDS.min, SECONDS.max),
          preset: job.preset,
          mode: job.mode,
          takes: 1,
          // Every slot is set, never merged: a leftover frame from the previous
          // draft would ride along into a job that has nothing to do with it.
          refs: frames ? [] : tiles,
          startFrame: frames ? tiles[0] ?? null : null,
          endFrame: frames ? tiles[1] ?? null : null,
        })
      },
      clearDraft: () => set({ prompt: '', refs: [], startFrame: null, endFrame: null }),
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
        initialized: s.initialized,
        refs: s.refs.filter(persistable).map(stripPreview),
        startFrame: s.startFrame && persistable(s.startFrame) ? stripPreview(s.startFrame) : null,
        endFrame: s.endFrame && persistable(s.endFrame) ? stripPreview(s.endFrame) : null,
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
  const { prompt, split, seconds, preset, mode, takes, refs, startFrame, endFrame } = state
  return { prompt, split, seconds, preset, mode, takes, refs, startFrame, endFrame }
}

export function clipCount(prompt: string, split: Split, takes: number): number {
  const prompts = split === 'lines' ? prompt.split('\n').filter((l) => l.trim()).length : prompt.trim() ? 1 : 0
  return prompts * takes
}

/** The tiles the current mode actually sends. Everything else is held, not used. */
export function tilesUsedBy(s: Draft): RefTile[] {
  if (s.mode === 'flf2v') return [s.startFrame, s.endFrame].filter(Boolean) as RefTile[]
  if (s.mode === 't2v') return []
  return s.refs
}

/** Why the queue button is disabled, or null when it is not. */
export type Block = 'prompt' | 'uploading' | 'upload-failed' | 'start-frame' | 'end-frame'

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
  }
  if (s.mode === 't2v') return { ...base, ref_images: [] }
  if (s.mode === 'flf2v') return { ...base, ref_images: readyKeys(tilesUsedBy(s)) }
  return { ...base, ref_images: readyKeys(s.refs) }
}
