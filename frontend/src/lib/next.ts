/**
 * Where to go after signing in. Only a same-origin path is honoured: anything
 * else - a full URL, or "//evil.example", which browsers read as a URL on
 * another host - falls back to the studio, so ?next= can't become an open
 * redirect.
 */
export function safeNext(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) {
    return '/'
  }
  return value
}
