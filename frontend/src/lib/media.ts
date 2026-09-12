/** Reference images are addressed by their storage key under the user's prefix. */
export function imageUrl(key: string): string {
  return `/api/image/${key.split('/').map(encodeURIComponent).join('/')}`
}

/** An extension's source is a short clip under the same prefix as the images.
 *  It needs a <video> to be seen: an <img> pointed at it is a broken icon. */
export function isVideoKey(key: string): boolean {
  return /\.(mp4|m4v|mov|webm)$/i.test(key)
}

export function downloadUrl(videoUrl: string): string {
  return `${videoUrl}?download=1`
}
