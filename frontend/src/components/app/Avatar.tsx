import { useState } from 'react'

import { cn } from '@/lib/utils'

const SIZES = {
  xs: 'size-6 text-2xs',
  sm: 'size-8 text-xs',
  lg: 'size-20 text-2xl',
} as const

/** A profile picture, or the email's first letter when there is none or it fails to load. */
export function Avatar({
  email,
  url,
  size = 'sm',
  alt = '',
  className,
}: {
  email: string
  url?: string | null
  size?: keyof typeof SIZES
  alt?: string
  className?: string
}) {
  // Remembered per URL, so a new picture gets a fresh try.
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  if (url && url !== failedUrl) {
    return (
      <img
        src={url}
        alt={alt}
        onError={() => setFailedUrl(url)}
        className={cn('shrink-0 rounded-full bg-secondary object-cover', SIZES[size], className)}
      />
    )
  }
  return (
    <span
      aria-hidden={alt ? undefined : true}
      aria-label={alt || undefined}
      role={alt ? 'img' : undefined}
      className={cn(
        'grid shrink-0 place-items-center rounded-full bg-primary font-semibold text-primary-foreground uppercase',
        SIZES[size],
        className,
      )}
    >
      {email.charAt(0)}
    </span>
  )
}
