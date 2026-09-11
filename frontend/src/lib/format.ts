// One place for every label and number format, so the same thing never reads
// two ways on two pages ("Generating" here, "running" there).
import type { JobStatus, Mode, PodState, Preset } from '@/api/types'

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  queued: 'Queued',
  running: 'Generating',
  done: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const POD_STATE_LABEL: Record<PodState, string> = {
  off: 'Off',
  booting: 'Starting',
  ready: 'Ready',
  stopping: 'Stopping',
  error: 'Error',
}

export const MODE_LABEL: Record<Mode, string> = {
  t2v: 'Text → video',
  i2v: 'Image → video',
  r2v: 'Reference → video',
}

const PRESET_NAMES: Record<string, string> = {
  draft: 'Draft',
  final: 'Final',
  turbo: 'Turbo',
  hd720: 'HD 720p',
}

export function presetLabel(key: string): string {
  return PRESET_NAMES[key] ?? key.charAt(0).toUpperCase() + key.slice(1)
}

/** What a preset delivers: its output size when it conforms, else its render size. */
export function presetSize(p: Pick<Preset, 'width' | 'height' | 'output_width' | 'output_height'>): string {
  return p.output_width && p.output_height ? `${p.output_width}×${p.output_height}` : `${p.width}×${p.height}`
}

export function fmtDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`
}

export function fmtUsd(amount: number, digits = 2): string {
  return `$${amount.toFixed(digits)}`
}

const dateTime = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** Epoch seconds (jobs) or an ISO string (users) to a local date and time. */
export function fmtWhen(value: number | string | null | undefined): string {
  if (value == null) return '—'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : dateTime.format(date)
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

export function fmtRelative(value: number | string | null | undefined, now = Date.now()): string {
  if (value == null) return '—'
  const ms = typeof value === 'number' ? value * 1000 : new Date(value).getTime()
  if (Number.isNaN(ms)) return '—'
  const diff = (ms - now) / 1000
  const abs = Math.abs(diff)
  if (abs < 45) return 'just now'
  if (abs < 3600) return relative.format(Math.round(diff / 60), 'minute')
  if (abs < 86400) return relative.format(Math.round(diff / 3600), 'hour')
  return relative.format(Math.round(diff / 86400), 'day')
}
