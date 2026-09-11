import { useEffect, useState } from 'react'

import { fmtDuration } from '@/lib/format'

/** Time since a start, ticking every second. Its own component so only it re-renders. */
export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return <span className="tabular-nums">{fmtDuration(now - since)}</span>
}
