import { ApiError } from '@/api/client'
import { t } from '@/i18n'

/** A message a person can read: the server's own words, capitalised. */
export function errorMessage(error: unknown): string {
  const raw =
    error instanceof ApiError || error instanceof Error ? error.message : t('errors.generic')
  const text = raw.trim() || t('errors.generic')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
