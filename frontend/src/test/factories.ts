import type { Job, Status } from '@/api/types'

let n = 0

export function makeJob(overrides: Partial<Job> = {}): Job {
  n += 1
  const id = overrides.id ?? `job-${n}`
  return {
    id,
    status: 'queued',
    prompt: `Prompt ${n}`,
    ref_images: [],
    seconds: 10,
    seed: null,
    mode: 'i2v',
    preset: 'final',
    created_at: 1_700_000_000 + n,
    started_at: null,
    finished_at: null,
    attempts: 0,
    error: null,
    bytes: null,
    queue_position: null,
    video_url: null,
    poster_url: null,
    ...overrides,
  }
}

export function makeStatus(overrides: Partial<Status> = {}): Status {
  return {
    pod: { state: 'off', detail: 'The GPU starts when there is something to render.', uptime_s: 0 },
    counts: { queued: 0, running: 0, done: 0, failed: 0, cancelled: 0 },
    queue: { total_queued: 0, total_running: 0 },
    backend: 'mock',
    notice: '',
    config: {
      presets: {
        draft: { width: 768, height: 432, steps: 20, lora: '', lora_strength: 1 },
        final: { width: 1344, height: 768, steps: 30, lora: '', lora_strength: 1 },
      },
      default_preset: 'final',
      default_mode: 'i2v',
      default_seconds: 10,
      fps: 24,
      mock: true,
    },
    user: { email: 'a@h3.local', role: 'user' },
    ...overrides,
  }
}
