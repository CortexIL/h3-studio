import { imageUrl, isVideoKey } from '@/lib/media'
import { cn } from '@/lib/utils'

interface Props {
  objectKey: string
  className?: string
  /** Accessible name. Empty means decorative, and the element is hidden from readers. */
  alt?: string
}

/**
 * One reference, as a thumbnail. Images are images; the clip an extension
 * continues is a video, and shows its first frame - the frame the extension
 * picks up from. The time fragment makes Safari paint that frame; other
 * browsers paint it anyway.
 */
export function RefThumb({ objectKey, className, alt = '' }: Props) {
  if (isVideoKey(objectKey)) {
    return (
      <video
        src={`${imageUrl(objectKey)}#t=0.001`}
        muted
        playsInline
        preload="metadata"
        aria-label={alt || undefined}
        aria-hidden={alt ? undefined : true}
        className={cn('bg-black object-cover', className)}
      />
    )
  }
  return <img src={imageUrl(objectKey)} alt={alt} loading="lazy" className={cn('object-cover', className)} />
}
