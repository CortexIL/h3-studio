import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { Job, Mode, PublicConfig } from '@/api/types'

export type Split = 'single' | 'lines'

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
}

interface ComposeState extends Draft {
  initialized: boolean
  setPrompt: (value: string) => void
  setSplit: (value: Split) => void
  setSeconds: (value: number) => void
  setPreset: (value: string) => void
  setMode: (value: Mode) => void
  setTakes: (value: number) => void
  addRef: (tile: RefTile) => void
  updateRef: (id: string, patch: Partial<RefTile>) => void
  removeRef: (id: string) => void
  applyDefaults: (config: PublicConfig) => void
  loadFromJob: (job: Pick<Job, 'prompt' | 'seconds' | 'preset' | 'mode' | 'ref_images'>) => void
  clearDraft: () => void
  restore: (draft: Draft) => void
}

export const SECONDS = { min: 4, max: 15 } as const
export const TAKES = { min: 1, max: 10 } as const

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, Math.round(Number.isFinite(value) ? value : min)))

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
      initialized: false,
      setPrompt: (prompt) => set({ prompt }),
      setSplit: (split) => set({ split }),
      setSeconds: (seconds) => set({ seconds: clamp(seconds, SECONDS.min, SECONDS.max) }),
      setPreset: (preset) => set({ preset }),
      setMode: (mode) => set({ mode }),
      setTakes: (takes) => set({ takes: clamp(takes, TAKES.min, TAKES.max) }),
      addRef: (tile) => set((s) => ({ refs: [...s.refs, tile] })),
      updateRef: (id, patch) =>
        set((s) => ({ refs: s.refs.map((r) => (r.id === id ? { ...r, ...patch } : r)) })),
      removeRef: (id) => set((s) => ({ refs: s.refs.filter((r) => r.id !== id) })),
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
      loadFromJob: (job) =>
        set({
          prompt: job.prompt,
          split: 'single',
          seconds: clamp(job.seconds, SECONDS.min, SECONDS.max),
          preset: job.preset,
          mode: job.mode,
          takes: 1,
          refs: job.ref_images.map((key, i) => ({
            id: `job-ref-${i}-${key}`,
            name: key.split('/').pop() ?? 'reference',
            status: 'ready' as const,
            progress: 1,
            key,
          })),
        }),
      clearDraft: () => set({ prompt: '', refs: [] }),
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
        refs: s.refs
          .filter((r) => r.status === 'ready' && r.key)
          .map(({ previewUrl: _preview, ...rest }) => rest),
      }),
    },
  ),
)

export function draftOf(state: Draft): Draft {
  const { prompt, split, seconds, preset, mode, takes, refs } = state
  return { prompt, split, seconds, preset, mode, takes, refs }
}

export function clipCount(prompt: string, split: Split, takes: number): number {
  const prompts = split === 'lines' ? prompt.split('\n').filter((l) => l.trim()).length : prompt.trim() ? 1 : 0
  return prompts * takes
}
