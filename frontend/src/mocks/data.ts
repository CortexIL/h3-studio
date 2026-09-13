// Realistic fixtures for the development mock API: a signed-in admin, jobs in
// every state, a filled archive, and an admin view with users and cost history.
import type { AdminStatus, AdminUser, Clip, Job, KeyState, Me, Run, Status } from '@/api/types'

const now = () => Date.now() / 1000

export const me: Me = { id: 'u-admin', email: 'you@h3.local', role: 'admin', avatar_url: null }

const PROMPTS = [
  'A red vintage car drives along a wet coastal road at dusk, headlights reflecting on the asphalt',
  'Slow dolly shot through a neon-lit Tokyo alley in the rain, steam rising from a noodle stall',
  'A lighthouse on a cliff during a storm, waves crashing, cinematic wide shot',
  'Close-up of a hummingbird drinking from a red flower, shallow depth of field',
  'Aerial shot over autumn forest with a winding river, golden hour light',
  'A cat wearing a tiny astronaut helmet floating in a spaceship cabin',
  'Time-lapse of clouds rolling over mountain peaks at sunrise',
  'A barista pouring latte art in slow motion, warm café lighting',
  'Street market in Marrakech, handheld camera walking through the crowd',
]

export function makeJobs(): Job[] {
  const t = now()
  const base = { ref_images: [], seed: null, attempts: 1, error: null, bytes: null, video_url: null, poster_url: null, queue_position: null, keep_audio: true, sound: null, music: null, steps: null, shift_video: null, shift_audio: null, width: null, height: null, keyframes: [], audio: null, ref_videos: [], ref_audios: [], source_job_id: null, upscale_factor: null }
  return [
    { ...base, id: 'j-queued-2', status: 'queued', prompt: PROMPTS[6]!, seconds: 10, mode: 't2v', preset: 'final', created_at: t - 20, started_at: null, finished_at: null, attempts: 0, queue_position: 2 },
    { ...base, id: 'j-queued-1', status: 'queued', prompt: PROMPTS[5]!, seconds: 6, mode: 'i2v', preset: 'turbo', created_at: t - 40, started_at: null, finished_at: null, attempts: 0, queue_position: 1 },
    { ...base, id: 'j-running', status: 'running', prompt: PROMPTS[1]!, seconds: 10, mode: 'i2v', preset: 'final', created_at: t - 200, started_at: t - 95, finished_at: null },
    { ...base, id: 'j-done-1', status: 'done', prompt: PROMPTS[0]!, seconds: 10, mode: 'i2v', preset: 'final', created_at: t - 900, started_at: t - 800, finished_at: t - 420, bytes: 8_400_000, video_url: '/api/video/j-done-1', poster_url: '/api/poster/j-done-1' },
    { ...base, id: 'j-failed', status: 'failed', prompt: PROMPTS[2]!, seconds: 15, mode: 't2v', preset: 'final', created_at: t - 1500, started_at: t - 1400, finished_at: t - 1300, attempts: 3, error: 'the render ran out of GPU memory' },
    { ...base, id: 'j-done-2', status: 'done', prompt: PROMPTS[3]!, seconds: 6, mode: 'r2v', preset: 'turbo', created_at: t - 3600, started_at: t - 3500, finished_at: t - 3300, bytes: 3_100_000, video_url: '/api/video/j-done-2', poster_url: '/api/poster/j-done-2' },
    { ...base, id: 'j-cancelled', status: 'cancelled', prompt: PROMPTS[4]!, seconds: 10, mode: 't2v', preset: 'draft', created_at: t - 7200, started_at: null, finished_at: t - 7100, attempts: 0 },
  ]
}

export function makeClips(): Clip[] {
  const t = now()
  const presets = ['final', 'turbo', 'draft'] as const
  const modes = ['i2v', 't2v', 'r2v'] as const
  return PROMPTS.map((prompt, i) => ({
    id: `c-${i}`,
    prompt,
    keep_audio: i % 4 !== 0,
    sound: null,
    music: null,
    steps: null,
    shift_video: null,
    shift_audio: null,
    width: null,
    height: null,
    keyframes: [],
    audio: null,
    ref_videos: [],
    ref_audios: [],
    source_job_id: null,
    upscale_factor: null,
    ref_images: [],
    seconds: [10, 6, 15][i % 3]!,
    seed: 1000 + i,
    preset: presets[i % 3]!,
    mode: modes[i % 3]!,
    created_at: t - 3600 * (i + 1) - 300,
    finished_at: t - 3600 * (i + 1),
    bytes: 2_000_000 + i * 900_000,
    video_url: `/api/video/c-${i}`,
    poster_url: `/api/poster/c-${i}`,
  }))
}

export function makeStatus(): Status {
  return {
    pod: { state: 'ready', detail: 'Ready.', uptime_s: 1840 },
    counts: { queued: 2, running: 1, done: 2, failed: 1, cancelled: 1 },
    queue: { total_queued: 3, total_running: 1 },
    backend: 'runpod',
    notice: '',
    config: {
      presets: {
        draft: { width: 768, height: 432, steps: 20, lora: '', lora_strength: 1, hidden: true },
        final: { width: 1344, height: 768, steps: 30, lora: '', lora_strength: 1 },
        turbo: { width: 1344, height: 768, steps: 4, lora: 'turbo.safetensors', lora_strength: 1 },
        hd720: { width: 1344, height: 768, steps: 30, lora: '', lora_strength: 1, output_width: 1280, output_height: 720 },
      },
      default_preset: 'final',
      default_mode: 'i2v',
      default_seconds: 10,
      fps: 24,
      mock: false,
      keep_audio: true,
      estimate: { gpu: 'NVIDIA L40', confidence: 'estimated', minutes_per_10s: { draft: 4.8, final: 65, turbo: 8.7, hd720: 65 } },
    },
    user: { email: me.email, role: me.role },
  }
}

export function makeUsers(): AdminUser[] {
  const t = now()
  const usage = (done: number, queued = 0, failed = 0, bytes = 0, last: number | null = t - 600) => ({
    queued, running: 0, done, failed, cancelled: 0, stored_bytes: bytes, last_job_at: last,
  })
  return [
    { id: 'u-admin', email: me.email, role: 'admin', is_active: true, token_version: 1, avatar_url: null, created_at: '2026-09-11T08:00:00Z', usage: usage(14, 2, 1, 96_000_000) },
    { id: 'u-2', email: 'dana@h3.local', role: 'user', is_active: true, token_version: 1, avatar_url: null, created_at: '2026-09-11T09:12:00Z', usage: usage(31, 0, 2, 240_000_000, t - 5400) },
    { id: 'u-3', email: 'omer@h3.local', role: 'user', is_active: true, token_version: 1, avatar_url: null, created_at: '2026-09-11T10:40:00Z', usage: usage(3, 1, 0, 18_000_000, t - 86400) },
    { id: 'u-4', email: 'guest@h3.local', role: 'user', is_active: false, token_version: 3, avatar_url: null, created_at: '2026-09-11T11:05:00Z', usage: usage(0, 0, 0, 0, null) },
  ]
}

export function makeAdminStatus(): AdminStatus {
  return {
    leader: true,
    policy: 'auto',
    pod: { state: 'ready', detail: 'NVIDIA GeForce RTX 5090 @ $0.89/hr', pod_id: 'pod-abc123', uptime_s: 1840, gpu: 'NVIDIA GeForce RTX 5090', rate_per_hour: 0.89 },
    counts: { queued: 3, running: 1, done: 48, failed: 3, cancelled: 2 },
    inflight: 1,
    session: { seconds: 1840, cost_usd: 0.46, limit_usd: 8, warn_usd: 5 },
    backend: 'runpod',
    error: '',
    notice: '',
  }
}

export function makeRuns(): Run[] {
  const t = now()
  return [
    { id: 'r-1', pod_id: 'pod-abc123', status: 'ready', endpoint: null, gpu_type: 'NVIDIA GeForce RTX 5090', started_at: t - 1840, ended_at: null, cost_estimate: 0.46, note: 'auto start' },
    { id: 'r-2', pod_id: 'pod-9f2e', status: 'stopped', endpoint: null, gpu_type: 'NVIDIA L40S', started_at: t - 90000, ended_at: t - 84000, cost_estimate: 1.32, note: 'idle 10 min' },
    { id: 'r-3', pod_id: 'pod-77aa', status: 'stopped', endpoint: null, gpu_type: 'NVIDIA GeForce RTX 5090', started_at: t - 180000, ended_at: t - 170000, cost_estimate: 2.47, note: 'budget ceiling $8.00 hit' },
  ]
}

export const keyState: KeyState = { present: true, hint: '4f2a' }
