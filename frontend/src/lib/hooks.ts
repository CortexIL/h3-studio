import { useEffect, useState } from 'react'

export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title === 'H3 Studio' ? title : `${title} · H3 Studio`
  }, [title])
}
