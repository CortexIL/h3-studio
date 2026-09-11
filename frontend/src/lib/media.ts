/** Reference images are addressed by their storage key under the user's prefix. */
export function imageUrl(key: string): string {
  return `/api/image/${key.split('/').map(encodeURIComponent).join('/')}`
}

export function downloadUrl(videoUrl: string): string {
  return `${videoUrl}?download=1`
}
