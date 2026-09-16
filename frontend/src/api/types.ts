// Hand-written: the Python routes return plain dicts, so the OpenAPI schema
// has no response models to generate these from. Job and clip timestamps are
// epoch seconds; user timestamps are ISO strings.

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type Mode = 't2v' | 'i2v' | 'r2v' | 'flf2v' | 'extend' | 'upscale'
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
  /** The motion / sound-variation values the shortcut was trained at; null = the model's own. */
  shift_video?: number | null
  shift_audio?: number | null
  /** The delivered size, when it differs from the rendered one. 0 = as rendered. */
  output_width?: number
  output_height?: number
  /** Only reachable through an action on a finished clip. */
  hidden?: boolean
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
  /** Render minutes per preset at the 10-second reference length, on the GPU the pod asks for first. */
  estimate: { gpu: string; confidence: 'estimated' | 'measured'; minutes_per_10s: Record<string, number> }
  /** Whether an admin has set up the prompt helper (a language-model key). */
  prompt_helper: boolean
}

/** The prompt helper's answer: the three fields of the model's own format. */
export interface PromptImprove {
  description: string
  sounds: string
  music: string
}

export interface Status {
  /** `ready` and `starting` count the GPUs when more than one can run at once. */
  pod: { state: PodState; detail: string; uptime_s: number; ready?: number; starting?: number }
  counts: Record<JobStatus, number>
  queue: { total_queued: number; total_running: number }
  backend: string
  notice: string
  config: PublicConfig
  user: { email: string; role: Role }
}

export interface Keyframe {
  key: string
  /** Seconds from the start of the clip. */
  at: number
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
  /** Render controls on top of the preset. null = the preset's own value. */
  steps: number | null
  shift_video: number | null
  shift_audio: number | null
  width: number | null
  height: number | null
  /** Images pinned at a moment inside the clip. */
  keyframes: Keyframe[]
  /** An audio track the clip follows (an upload key), or null. */
  audio: string | null
  /** References mode: reference videos and standalone audio clips. */
  ref_videos: string[]
  ref_audios: string[]
  /** Effect presets the clip picked, by name. */
  effects: string[]
  /** For an upscale: the clip it enlarges and by how much. */
  source_job_id: string | null
  upscale_factor: number | null
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
  /** Render controls on top of the preset. null = the preset's own value. */
  steps: number | null
  shift_video: number | null
  shift_audio: number | null
  width: number | null
  height: number | null
  /** Images pinned at a moment inside the clip. */
  keyframes: Keyframe[]
  /** An audio track the clip follows (an upload key), or null. */
  audio: string | null
  /** References mode: reference videos and standalone audio clips. */
  ref_videos: string[]
  ref_audios: string[]
  /** Effect presets the clip picked, by name. */
  effects: string[]
  /** For an upscale: the clip it enlarges and by how much. */
  source_job_id: string | null
  upscale_factor: number | null
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

/** The four totals behind the feed's filters, counted by the server over the
 *  whole feed rather than over the page below - the page has a ceiling. */
export interface JobCounts {
  all: number
  active: number
  ready: number
  failed: number
}

export interface JobsPage {
  jobs: Job[]
  /** Absent from an older server; the feed falls back to counting the page. */
  counts?: JobCounts
  shown?: number
  limit?: number
  has_more?: boolean
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
  steps?: number
  shift_video?: number
  shift_audio?: number
  width?: number
  height?: number
  keyframes?: Keyframe[]
  audio?: string
  ref_videos?: string[]
  ref_audios?: string[]
  effects?: string[]
}

export interface Estimate {
  clips: number
  gpu: string
  rate_per_hour: number
  minutes_per_clip: number
  render_minutes: number
  startup_minutes: number
  /** How many GPUs the batch would run on at once. */
  pods: number
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

/** One rented GPU, as the admin page lists it. */
export interface PodInfo {
  number: number
  state: PodState
  detail: string
  pod_id: string | null
  uptime_s: number
  gpu: string
  rate_per_hour: number
  cost_usd: number
  /** Clips rendering on it right now. */
  rendering: number
}

export interface AdminStatus {
  leader: boolean
  policy: Policy
  /** The experimental faster attention, applied to every clip while on. */
  sage: boolean
  pod: {
    state: PodState
    detail: string
    pod_id: string | null
    uptime_s: number
    gpu: string
    rate_per_hour: number
  }
  pods: PodInfo[]
  /** How many GPUs may run at once, and the most this server allows. */
  max_pods: number
  max_pods_allowed: number
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
