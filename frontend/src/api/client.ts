export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Indirection so tests can observe the sign-in redirect instead of navigating. */
export const navigation = {
  replace(url: string) {
    window.location.replace(url)
  },
  toLogin(url: string) {
    navigation.replace(url)
  },
}

let redirecting = false

function signInUrl(): string {
  const here = window.location.pathname + window.location.search
  return here === '/' ? '/login' : `/login?next=${encodeURIComponent(here)}`
}

/**
 * A 401 means the session is gone - except on the sign-in routes themselves,
 * where it means "wrong password" and must reach the form.
 */
function handleUnauthorized(path: string): void {
  if (path.startsWith('/api/auth/') || window.location.pathname === '/login') return
  if (redirecting) return
  redirecting = true
  navigation.toLogin(signInUrl())
}

async function detailOf(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
      // FastAPI validation errors: [{loc, msg, type}, ...]
      if (Array.isArray(detail)) {
        const messages = detail
          .map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : ''))
          .filter(Boolean)
        if (messages.length) return messages.join('; ')
      }
    }
  } catch {
    // not JSON - fall through
  }
  return res.statusText || `Request failed (${res.status})`
}

export interface RequestOptions {
  method?: string
  json?: unknown
  body?: BodyInit
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const init: RequestInit = {
    method: options.method ?? 'GET',
    credentials: 'same-origin',
  }
  if (options.signal) init.signal = options.signal
  if (options.json !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(options.json)
  } else if (options.body !== undefined) {
    init.body = options.body
  }
  const res = await fetch(path, init)
  if (res.status === 401) {
    handleUnauthorized(path)
    throw new ApiError(401, await detailOf(res))
  }
  if (!res.ok) throw new ApiError(res.status, await detailOf(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/**
 * Multipart upload with progress. XHR rather than fetch, because fetch cannot
 * report upload progress and a batch zip can be hundreds of megabytes.
 */
export function uploadWithProgress<T>(
  path: string,
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.withCredentials = true
    xhr.responseType = 'json'
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total)
      }
    }
    xhr.onload = () => {
      const body = xhr.response as { detail?: unknown } | null
      if (xhr.status === 401) handleUnauthorized(path)
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as T)
        return
      }
      const detail = typeof body?.detail === 'string' ? body.detail : `Upload failed (${xhr.status})`
      reject(new ApiError(xhr.status, detail))
    }
    xhr.onerror = () => reject(new ApiError(0, 'Could not reach the server'))
    xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled'))
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    const form = new FormData()
    form.append('file', file, file.name || 'upload')
    xhr.send(form)
  })
}

/** Test-only: forget that a redirect already happened. */
export function resetRedirectGuard(): void {
  redirecting = false
}
