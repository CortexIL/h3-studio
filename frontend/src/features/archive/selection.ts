// The parts of selecting clips that are arithmetic rather than React, kept apart
// so they can be tested: jsdom has no layout, so anything that asks the browser
// where an element is can only be exercised by hand.

export interface Box {
  left: number
  top: number
  right: number
  bottom: number
}

/** Every id from `a` to `b` inclusive, whichever order they were clicked in. */
export function rangeBetween(ids: string[], a: string, b: string): string[] {
  const from = ids.indexOf(a)
  const to = ids.indexOf(b)
  if (from < 0 || to < 0) return []
  return ids.slice(Math.min(from, to), Math.max(from, to) + 1)
}

/** True when two rectangles share any area at all. */
export function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
}

/** The ids whose boxes the band touches. Touching is enough - a rubber band that
 *  demanded full containment would miss every card on the edges of the sweep. */
export function idsInBand(boxes: { id: string; box: Box }[], band: Box): string[] {
  return boxes.filter((c) => overlaps(c.box, band)).map((c) => c.id)
}

/** A drag becomes a band rather than a click once it has travelled this far. */
export const BAND_THRESHOLD = 6

export function bandFrom(start: { x: number; y: number }, x: number, y: number): Box {
  return {
    left: Math.min(start.x, x),
    top: Math.min(start.y, y),
    right: Math.max(start.x, x),
    bottom: Math.max(start.y, y),
  }
}
