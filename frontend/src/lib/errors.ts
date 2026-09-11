import { ApiError } from '@/api/client'

/** A message a person can read: the server's own words, capitalised. */
export function errorMessage(error: unknown): string {
  const raw =
    error instanceof ApiError || error instanceof Error ? error.message : 'Something went wrong'
  const text = raw.trim() || 'Something went wrong'
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
