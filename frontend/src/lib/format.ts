// One place for every label and number format, so the same thing never reads
// two ways on two pages ("Generating" here, "running" there). Labels come from
// the dictionary, so they follow the chosen language.
import type { JobStatus, Mode, PodState, Preset } from '@/api/types'
import { intlLocale, t, type Key } from '@/i18n'
import { useLayout } from '@/lib/layout'

export function jobStatusLabel(status: JobStatus): string {
  return t(`status.${status}` as Key)
}

export function podStateLabel(state: PodState): string {
  return t(`pod.state.${state}` as Key)
}

const MODES: Mode[] = ['i2v', 't2v', 'flf2v', 'extend', 'r2v', 'upscale']

/** The name for a mode read off a stored clip.
 *
 * Clips outlive builds: rolling back while a row carries a newer mode must not
 * render a blank badge, so an unknown mode reads as itself.
 */
export function modeLabel(mode: string): string {
  if (!MODES.includes(mode as Mode)) return mode
  // The classic layout keeps the names the studio had before the beta.
  const classic = useLayout.getState().layout === 'classic'
  return t((classic ? `classic.mode.${mode}` : `mode.${mode}`) as Key)
}

const PRESETS = ['draft', 'final', 'turbo', 'balanced', 'hd720', 'hd1080', 'up2x', 'hd1080up']

export function presetLabel(key: string): string {
  if (!PRESETS.includes(key)) return key.charAt(0).toUpperCase() + key.slice(1)
  const classic = useLayout.getState().layout === 'classic'
  return t((classic ? `classic.preset.${key}` : `preset.${key}`) as Key)
}

/** What a preset delivers: its output size when it conforms, else its render size. */
export function presetSize(p: Pick<Preset, 'width' | 'height' | 'output_width' | 'output_height'>): string {
  return p.output_width && p.output_height ? `${p.output_width}×${p.output_height}` : `${p.width}×${p.height}`
}

export function fmtDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  if (s < 60) return t('time.seconds', { n: s })
  const m = Math.floor(s / 60)
  if (m < 60) return t('time.minutesSeconds', { m, s: s % 60 })
  return t('time.hoursMinutes', { h: Math.floor(m / 60), m: m % 60 })
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

// Formatters are cheap to make and depend on the language, so they are made
// per call rather than once at import.
const dateTime = () => new Intl.DateTimeFormat(intlLocale(), { dateStyle: 'medium', timeStyle: 'short' })
const relative = () => new Intl.RelativeTimeFormat(intlLocale(), { numeric: 'auto' })

/** Epoch seconds (jobs) or an ISO string (users) to a local date and time. */
export function fmtWhen(value: number | string | null | undefined): string {
  if (value == null) return '—'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : dateTime().format(date)
}

export function fmtRelative(value: number | string | null | undefined, now = Date.now()): string {
  if (value == null) return '—'
  const ms = typeof value === 'number' ? value * 1000 : new Date(value).getTime()
  if (Number.isNaN(ms)) return '—'
  const diff = (ms - now) / 1000
  const abs = Math.abs(diff)
  if (abs < 45) return t('time.justNow')
  if (abs < 3600) return relative().format(Math.round(diff / 60), 'minute')
  if (abs < 86400) return relative().format(Math.round(diff / 3600), 'hour')
  return relative().format(Math.round(diff / 86400), 'day')
}
