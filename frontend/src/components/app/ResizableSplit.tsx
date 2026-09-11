import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'

// The same key the old page used, so everyone's saved width carries over.
const KEY = 'h3.leftWidth'
const MIN = 280
const MAX = 680
const DEFAULT = 380
const STEP = 16

function readWidth(): number {
  try {
    const saved = Number.parseInt(localStorage.getItem(KEY) ?? '', 10)
    return saved >= MIN && saved <= MAX ? saved : DEFAULT
  } catch {
    return DEFAULT
  }
}

function saveWidth(width: number | null) {
  try {
    if (width === null) localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, String(width))
  } catch {
    // storage blocked: the width just isn't remembered
  }
}

/**
 * Two panes side by side with a draggable divider on wide screens, stacked on
 * narrow ones. The divider works with a mouse, the arrow keys, Home/End, and a
 * double-click that puts it back.
 */
export function ResizableSplit({ left, right }: { left: ReactNode; right: ReactNode }) {
  const [width, setWidth] = useState(readWidth)
  const start = useRef<{ x: number; width: number } | null>(null)

  const set = useCallback((next: number) => {
    const clamped = Math.min(MAX, Math.max(MIN, Math.round(next)))
    setWidth(clamped)
    return clamped
  }, [])

  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (start.current) set(start.current.width + e.clientX - start.current.x)
    }
    const up = () => {
      if (!start.current) return
      start.current = null
      document.body.style.removeProperty('cursor')
      document.body.style.removeProperty('user-select')
      setWidth((w) => {
        saveWidth(w)
        return w
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [set])

  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-3 lg:grid lg:grid-cols-[var(--left)_12px_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)] lg:gap-0"
      style={{ '--left': `${width}px` } as CSSProperties}
    >
      <div className="flex min-h-0 flex-col">{left}</div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the create panel"
        aria-valuemin={MIN}
        aria-valuemax={MAX}
        aria-valuenow={width}
        tabIndex={0}
        title="Drag to resize · double-click to reset"
        className="group hidden cursor-col-resize items-center justify-center lg:flex"
        onPointerDown={(e) => {
          e.preventDefault()
          start.current = { x: e.clientX, width }
          document.body.style.cursor = 'col-resize'
          document.body.style.userSelect = 'none'
        }}
        onDoubleClick={() => {
          setWidth(DEFAULT)
          saveWidth(null)
        }}
        onKeyDown={(e) => {
          const step = e.shiftKey ? STEP * 3 : STEP
          let next: number | null = null
          if (e.key === 'ArrowLeft') next = width - step
          if (e.key === 'ArrowRight') next = width + step
          if (e.key === 'Home') next = MIN
          if (e.key === 'End') next = MAX
          if (next !== null) {
            e.preventDefault()
            saveWidth(set(next))
          }
        }}
      >
        <span className="h-10 w-1 rounded-full bg-border transition-colors group-hover:bg-primary group-focus-visible:bg-primary" />
      </div>
      <div className="flex min-h-0 flex-col">{right}</div>
    </div>
  )
}
