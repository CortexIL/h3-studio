import { useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'

/**
 * A video that asks for nothing until it is near the viewport. A feed of forty
 * clips would otherwise open forty range requests before anyone presses play.
 *
 * The src never changes after mount - only preload does - and changing preload
 * does not reload a video, so a poll re-rendering the card leaves playback alone.
 */
export function LazyVideo({
  src,
  poster,
  label,
  className,
}: {
  src: string
  poster?: string | null
  label: string
  className?: string
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined')

  useEffect(() => {
    const el = ref.current
    if (!el || near) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true)
          observer.disconnect()
        }
      },
      { rootMargin: '300px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [near])

  return (
    <video
      ref={ref}
      src={src}
      poster={poster ?? undefined}
      controls
      playsInline
      preload={near ? 'metadata' : 'none'}
      aria-label={label}
      className={cn('block aspect-video w-full bg-black object-contain', className)}
    />
  )
}
