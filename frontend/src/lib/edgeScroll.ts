/**
 * Keep scrolling while a drag is held against an edge.
 *
 * A sweep or a reorder that reaches the bottom of the window has nowhere left to
 * go: the pointer cannot leave the screen, so without this the last rows of a
 * long list are unreachable while the button is down. Every file manager scrolls
 * for you instead, and so does this.
 */

/** How close to an edge the pointer must come, and the fastest it may scroll. */
const EDGE = 72
const MAX_STEP = 26

/** The nearest ancestor that actually scrolls, or null when the window does. */
export function scrollParent(el: Element | null): HTMLElement | null {
  for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
    const overflow = getComputedStyle(node).overflowY
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight) {
      return node
    }
  }
  return null
}

export interface EdgeScroller {
  /** Where the pointer is now, in viewport coordinates. Starts scrolling if it is at an edge. */
  track(clientY: number): void
  stop(): void
}

/**
 * `container` is the element that scrolls, or null for the window. `onScroll`
 * runs after every step, because whatever the drag is drawing - a band, a drop
 * line - is computed from positions that the scroll has just moved.
 */
export function edgeScroller(container: HTMLElement | null, onScroll?: () => void): EdgeScroller {
  let y: number | null = null
  let frame = 0

  const step = () => {
    frame = 0
    if (y === null) return
    const top = container ? container.getBoundingClientRect().top : 0
    const bottom = container ? container.getBoundingClientRect().bottom : window.innerHeight
    // How far past the edge zone the pointer is, negative at the top.
    const over = y > bottom - EDGE ? y - (bottom - EDGE) : y < top + EDGE ? y - (top + EDGE) : 0
    if (over !== 0) {
      // Nearer the edge is faster, so a sweep can creep or race.
      const delta = Math.sign(over) * Math.min(MAX_STEP, Math.max(2, Math.round(Math.abs(over) / 2)))
      if (container) container.scrollTop += delta
      else window.scrollBy(0, delta)
      onScroll?.()
    }
    frame = requestAnimationFrame(step)
  }

  return {
    track(clientY: number) {
      y = clientY
      if (!frame) frame = requestAnimationFrame(step)
    },
    stop() {
      y = null
      if (frame) cancelAnimationFrame(frame)
      frame = 0
    },
  }
}
