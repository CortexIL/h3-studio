// Hand-written: the Python routes return plain dicts, so the OpenAPI schema
// has no response models to generate these from. Job and clip timestamps are
// epoch seconds; user timestamps are ISO strings.

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type Mode = 't2v' | 'i2v' | 'r2v' | 'flf2v' | 'extend'
export type PodState = 'off' | 'booting' | 'ready' | 'stopping' | 'error'
export type Role = 'user' | 'admin'
export type Policy = 'auto' | 'keep-warm' | 'off'

export interface Me {
  id: string
  email: string
  role: Role
  avatar_url: string | null
}

export interface Preset {
  width: number
  height: number
  steps: number
  lora: string
  lora_strength: number
  /** The delivered size, when it differs from the rendered one. 0 = as rendered. */
  output_width?: number
  output_height?: number
}

export interface PublicConfig {
  presets: Record<string, Preset>
  default_preset: string
  default_mode: Mode
  default_seconds: number
  fps: number
  mock: boolean
  /** Where the composer's sound switch starts, for a clip that says nothing. */
  keep_audio: boolean
}

export interface Status {
  pod: { state: PodState; detail: string; uptime_s: number }
  counts: Record<JobStatus, number>
  queue: { total_queued: number; total_running: number }
  backend: string
  notice: string
  config: PublicConfig
  user: { email: string; role: Role }
}

export interface Job {
  id: string
  status: JobStatus
  prompt: string
  ref_images: string[]
  seconds: number
  seed: number | null
  mode: Mode
  preset: string
  /** null means the clip made no choice and followed the server's setting. */
  keep_audio: boolean | null
  /** Sound direction: the soundscape and the music, when the clip gave them. */
  sound: string | null
  music: string | null
  created_at: number
  started_at: number | null
  finished_at: number | null
  attempts: number
  error: string | null
  bytes: number | null
  queue_position: number | null
  video_url: string | null
  poster_url: string | null
}

export interface Clip {
  id: string
  prompt: string
  ref_images: string[]
  seconds: number
  seed: number | null
  preset: string
  mode: Mode
  keep_audio: boolean | null
  /** Sound direction: the soundscape and the music, when the clip gave them. */
  sound: string | null
  music: string | null
  created_at: number | null
  finished_at: number | null
  bytes: number | null
  video_url: string | null
  poster_url: string | null
}

export interface ArchivePage {
  clips: Clip[]
  next_cursor: string | null
}

export interface NewJobsBody {
  prompts: string
  split: 'single' | 'lines'
  seconds?: number
  preset?: string
  mode?: Mode
  count?: number
  seed?: number
  ref_images?: string[]
  keep_audio?: boolean
  sound?: string
  music?: string
}

export interface Estimate {
  clips: number
  gpu: string
  rate_per_hour: number
  minutes_per_clip: number
  render_minutes: number
  startup_minutes: number
  total_minutes: number
  cost_usd: number
  cost_per_clip_usd: number
  confidence: 'estimated' | 'measured'
}

export interface UploadResult {
  key: string
  name: string
}

export interface BatchResult {
  queued: number
  images: Record<string, string>
  missing_images: string[]
}

export interface Usage {
  queued: number
  running: number
  done: number
  failed: number
  cancelled: number
  stored_bytes: number
  last_job_at: number | null
}

export interface AdminUser {
  id: string
  email: string
  role: Role
  is_active: boolean
  token_version: number
  created_at: string
  avatar_url: string | null
  usage: Usage
}

export interface AdminStatus {
  leader: boolean
  policy: Policy
  pod: {
    state: PodState
    detail: string
    pod_id: string | null
    uptime_s: number
    gpu: string
    rate_per_hour: number
  }
  counts: Record<JobStatus, number>
  inflight: number
  session: { seconds: number; cost_usd: number; limit_usd: number; warn_usd: number }
  backend: string
  error: string
  notice: string
}

export interface Run {
  id: string
  pod_id: string | null
  status: string
  endpoint: string | null
  gpu_type: string | null
  started_at: number | null
  ended_at: number | null
  cost_estimate: number
  note: string | null
}

export interface KeyState {
  present: boolean
  hint: string
}
